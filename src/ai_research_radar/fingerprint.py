"""Fingerprinting, URL canonicalization and near-duplicate detection primitives.

Deduplication strategy (three layers):

1. Exact URL duplicates        -> canonicalized-URL equality.
2. Syndicated copies           -> identical normalized *titles* from other outlets
                                  (news aggregators republish headlines verbatim).
3. Same-story paraphrases      -> 64-bit simhash over title+body text; items whose
                                  hashes sit within a small Hamming distance are
                                  treated as descriptions of the same announcement.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

# Tracking parameters commonly appended by feeds/aggregators.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eit", "ref", "ref_src",
    "referrer", "igshid", "yclid", "_hsenc", "_hsmi", "vero_id", "wickedid",
}

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "its", "of", "on", "or", "that",
    "the", "their", "there", "these", "they", "this", "to", "was", "were",
    "will", "with",
})


def canonicalize_url(url: str) -> str:
    """Return a stable form of *url* ignoring tracking noise.

    Lowercases scheme/host, drops default ports, fragments, session-ish query
    parameters, and trailing slashes. Raises ValueError on unusable input.
    """
    url = url.strip()
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"not an absolute http(s) URL: {url!r}")
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"URL without host: {url!r}")
    netloc = host.lower()
    port = parts.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port is not None and not default_port:
        netloc = f"{netloc}:{port}"
    if parts.username:
        userinfo = quote(parts.username, safe="")
        if parts.password:
            userinfo += ":" + quote(parts.password, safe="")
        netloc = f"{userinfo}@{netloc}"

    path = unquote(parts.path)
    while len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    kept.sort(key=lambda kv: kv[0].lower())
    query = urlencode(kept)
    return urlunsplit((scheme, netloc, quote(path), query, ""))


def url_hash(url: str) -> str:
    """SHA-256 of the canonicalized URL — the exact-duplicate key."""
    return hashlib.sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


def normalized_words(text: str) -> list[str]:
    """Lowercased alphanumeric word list minus stopwords."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


def title_key(title: str) -> str:
    """Canonical title key used to detect verbatim syndication."""
    return " ".join(normalized_words(title))


def content_fingerprint(title: str, url: str, content: str = "") -> str:
    """Stable primary key for an item: hash of canonical title + canonical URL."""
    payload = "\x00".join(
        (title_key(title), canonicalize_url(url), title_key(content[:400]))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Simhash — cheap cosine-ish similarity over bag-of-words features
# ---------------------------------------------------------------------------

def _features(text: str) -> list[str]:
    words = normalized_words(text)
    grams = [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
    return words + grams


def simhash64(text: str) -> int:
    """63-bit simhash of *text* (empty text hashes to 0).

    63 instead of 64 bits so every value stays inside SQLite's signed
    INTEGER range; the lost bit is irrelevant for near-duplicate detection.
    """
    nbits = 63
    bits = [0] * nbits
    for feat in _features(text):
        h = int.from_bytes(hashlib.sha1(feat.encode("utf-8")).digest()[:8], "big")
        for i in range(nbits):
            bits[i] += 1 if (h >> i) & 1 else -1
    value = 0
    for i, b in enumerate(bits):
        if b > 0:
            value |= 1 << i
    return value


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def similarity_within(a: str, b: str, max_distance: int = 6) -> bool:
    """True when two texts are near-duplicates under simhash."""
    return hamming_distance(simhash64(a), simhash64(b)) <= max_distance


def word_jaccard(a: str, b: str) -> float:
    """Jaccard overlap of normalized word sets — second opinion for fuzzy dups."""
    wa, wb = set(normalized_words(a)), set(normalized_words(b))
    union = wa | wb
    return len(wa & wb) / len(union) if union else 0.0
