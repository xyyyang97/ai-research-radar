"""Common data model for every collected intelligence item.

Every adapter must produce ``RawItem`` objects; the pipeline upgrades them to
stored ``Item`` rows once fingerprints and scores have been computed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SOURCE_TYPES = frozenset({"rss", "github", "page", "youtube"})


@dataclass(slots=True)
class RawItem:
    """A single normalized piece of information from any source."""

    source: str                 # human label, e.g. "Anthropic News"
    source_type: str            # rss | github | page | youtube
    title: str
    url: str
    published_at: str           # ISO-8601 string, UTC
    author: str = ""
    raw_content: str = ""       # cleaned text body (untrusted external data!)
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    importance_score: int = 0
    reason_for_score: str = ""
    fingerprint: str = ""

    def validate(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(f"unknown source_type: {self.source_type!r}")
        if not self.title:
            raise ValueError(f"{self.source}: item with empty title ({self.url})")
        if not self.url:
            raise ValueError(f"{self.source}: item with empty url ({self.title!r})")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Columns persisted in SQLite (order matters for insert parameter packing).
ITEM_COLUMNS = (
    "id",
    "fingerprint",
    "url_hash",
    "title",
    "url",
    "source",
    "source_type",
    "published_at",
    "author",
    "raw_content",
    "summary",
    "topics",
    "importance_score",
    "reason_for_score",
    "title_key",
    "content_simhash",
    "first_seen",
    "status",
)
