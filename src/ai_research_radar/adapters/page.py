"""Plain web-page adapter — for blogs/sites without RSS.

Strategy (stdlib-only, no fragile scraping framework):

* fetch the page with the hardened net layer;
* extract article-like links (anchor text + href) restricted to the same host
  so nav/footer links to other sites are dropped;
* score candidate links by title length/keywords, keep the most article-like;
* best-effort date discovery from nearby text or <time datetime=…> attributes.

Prefer an official feed whenever one exists — this adapter is the fallback.
"""

from __future__ import annotations

import re
import urllib.parse

from .. import net
from ..config import RadarConfig
from ..models import RawItem
from .base import _TAG_RE, FetchResult, SourceAdapter, strip_tags, truncate

_A_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>",
                   re.IGNORECASE | re.DOTALL)
_TIME_RE = re.compile(r"<time\b[^>]*datetime=[\"']([^\"']+)[\"']", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_JUNK_WORDS = (
    "login", "signup", "sign in", "register", "privacy", "terms", "cookie",
    "contact", "about us", "careers", "press kit", "subscribe", "newsletter",
    "twitter", "github.com", "linkedin", "cookie policy", "status",
)

_MIN_TITLE = 12


_DATE_IN_TITLE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
)


def _clean_title(raw_title: str) -> str:
    """Page cards often bake category/date/summary into the anchor text.

    Prefer the inner heading if one exists, then strip a leading date prefix.
    """
    title = raw_title
    m = re.search(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", raw_title, re.IGNORECASE | re.DOTALL)
    if m:
        title = _TAG_RE.sub(" ", m.group(1))
        title = (
            title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        )
    title = re.sub(r"\s+", " ", title).strip()
    # drop leading "Product Jul 24, 2026" style prefixes when more text follows
    m2 = _DATE_IN_TITLE_RE.search(title)
    if m2 and len(title) > m2.end() + 10:
        title = title[m2.end() :].lstrip(" -—:")
    return title


class PageAdapter(SourceAdapter):
    source_type = "page"

    def __init__(self, url: str, name: str = "", link_selector: str = "",
                 config: RadarConfig | None = None) -> None:
        super().__init__(config)
        self.url = url
        self.name_override = name or ""
        self.link_selector = link_selector  # informational; regex extraction is used

    def _host(self) -> str:
        return urllib.parse.urlsplit(self.url).netloc.lower()

    def fetch(self) -> FetchResult:
        errors: list[str] = []
        try:
            resp = net.fetch(self.url, timeout=self.timeout)
        except RuntimeError as exc:
            return FetchResult(items=[], errors=[f"{self.url}: {exc}"])

        html = resp.text
        base_host = self._host()
        candidates: list[tuple[str, str]] = []  # (url, raw anchor inner HTML)
        seen: set[str] = set()
        for m in _A_RE.finditer(html):
            href = urllib.parse.urljoin(self.url, m.group(1).strip())
            raw_inner = m.group(2)
            text = strip_tags(raw_inner).strip()
            if not text or len(text) < _MIN_TITLE:
                continue
            parsed = urllib.parse.urlsplit(href)
            if parsed.netloc.lower() != base_host or parsed.scheme not in ("http", "https"):
                continue
            low = text.lower()
            path = parsed.path.lower()
            if any(j in low or j in path for j in _JUNK_WORDS):
                continue
            if href in seen:
                continue
            seen.add(href)
            candidates.append((href, raw_inner))

        # article-like pages usually live under blog/news/posts paths — prefer them,
        # but keep other same-host links if too few match.
        preferred = [c for c in candidates if re.search(
            r"/(blog|news|posts?|articles?|engineering|announcements?|releases?)", c[0],
            re.IGNORECASE)]
        chosen = preferred if len(preferred) >= 3 else candidates
        chosen = chosen[: self.max_items]

        page_dates = [m.group(1) for m in _TIME_RE.finditer(html)] or \
                     [m.group(1) for m in _DATE_RE.finditer(html)]
        default_date = page_dates[0] + "T00:00:00Z" if page_dates else ""

        source_name = self.name_override or base_host
        items = []
        for href, raw_title in chosen:
            title = _clean_title(raw_title)
            if len(title) < _MIN_TITLE:
                continue
            items.append(
                RawItem(
                    source=source_name,
                    source_type="page",
                    title=title[:500],
                    url=href,
                    published_at=default_date,
                    raw_content=truncate(title),
                )
            )
        return FetchResult(items=items, errors=errors)
