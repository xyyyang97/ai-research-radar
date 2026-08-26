"""Three-layer deduplication.

Layer 1 — exact URL:      canonicalized-URL equality (handled at insert time via
                          fingerprints that include the canonical URL).
Layer 2 — syndication:    identical normalized titles across different outlets.
Layer 3 — paraphrase:     simhash near-duplicates within Hamming distance budget.

When duplicates are found the *richest* copy is kept as primary and every other
occurrence is preserved as a corroborating reference
(``sources`` list on the cluster), so "multiple sources describing the same
announcement" strengthens rather than discards information.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fingerprint import hamming_distance, word_jaccard

# Fuzzy (simhash) merging is only trusted for texts substantial enough that
# closeness means something: short stubs like "repo release v0.21.1" vs
# "...v0.21.0" hash almost identically and are NOT duplicates.
# Empirical gap on real corpora: lightly-edited syndication lands at Hamming
# distance ~10-16 / Jaccard >= 0.74; unrelated articles sit at 25+.
_FUZZY_MIN_CHARS = 200
_FUZZY_MIN_JACCARD = 0.70


@dataclass(slots=True)
class DuplicateGroup:
    """A set of stored rows describing the same story."""

    primary_id: int
    member_ids: list[int] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)  # {source,url,title}


def _row_refs(rows: list[dict]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for r in rows:
        ref = {"source": r["source"], "url": r["url"], "title": r["title"]}
        if not any(x["url"] == ref["url"] for x in refs):
            refs.append(ref)
    return refs


def _is_github_event_url(url: str) -> bool:
    """Release/tag/activity pages are structured facts, not prose articles."""
    return "/releases/" in url or url.endswith("/commits")


def find_duplicates(rows: list[dict], threshold: int) -> list[DuplicateGroup]:
    """Cluster *rows* (dicts from db.all_rows()) into duplicate groups.

    Rows are expected sorted by importance DESC then date DESC, which makes the
    first element of a group the natural primary candidate; we additionally
    prefer the row with the longest raw_content (richest copy).
    """
    groups: list[DuplicateGroup] = []
    assigned: dict[int, int] = {}  # id -> group index

    def _richness(row: dict) -> tuple[int, int]:
        return (len(row.get("raw_content") or ""), row["importance_score"])

    for i, row in enumerate(rows):
        gid = assigned.get(row["id"])
        if gid is not None:
            continue

        group = DuplicateGroup(primary_id=row["id"], member_ids=[row["id"]])
        assigned[row["id"]] = len(groups)
        members: list[dict] = [row]

        title_key = (row.get("title_key") or "").strip()
        simhash = row.get("content_simhash") or 0
        row_text = f"{row['title']} {(row.get('raw_content') or '')[:600]}"

        for other in rows[i + 1 :]:
            if other["id"] in assigned:
                continue
            other_text = f"{other['title']} {(other.get('raw_content') or '')[:600]}"
            is_dup = False
            # Layer 2: verbatim syndicated headline
            otk = (other.get("title_key") or "").strip()
            if (title_key and otk and title_key == otk) or (
                simhash
                and other.get("content_simhash")
                and len(row_text) >= _FUZZY_MIN_CHARS
                and len(other_text) >= _FUZZY_MIN_CHARS
                and hamming_distance(simhash, other["content_simhash"]) <= threshold
                and word_jaccard(row_text, other_text) >= _FUZZY_MIN_JACCARD
            ):
                is_dup = True
            # Structured GitHub events (releases/tags/activity) are distinct
            # facts unless they point at the very same page: "release v0.124"
            # and "release v0.125" hash nearly identically but are different
            # announcements — even across different repos.
            if (
                is_dup
                and _is_github_event_url(row["url"])
                and _is_github_event_url(other["url"])
                and canonical_or_raw(row["url"]) != canonical_or_raw(other["url"])
            ):
                is_dup = False
            if not is_dup:
                continue
            assigned[other["id"]] = len(groups)
            group.member_ids.append(other["id"])
            members.append(other)

        if len(group.member_ids) > 1:
            # keep the richest copy as primary
            best = max(members, key=_richness)
            group.primary_id = best["id"]
            group.references = [
                r for r in _row_refs(members) if r["url"] != best["url"]
            ]
        groups.append(group)

    return groups


def canonical_or_raw(url: str) -> str:
    try:
        from .fingerprint import canonicalize_url

        return canonicalize_url(url)
    except ValueError:
        return url
