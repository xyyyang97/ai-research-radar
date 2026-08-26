"""Source adapter interface + registry.

Adding a new source type means writing one class with a single method and
registering it — no pipeline code changes. See docs/extending.md.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from ..config import RadarConfig
from ..models import RawItem

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class FetchResult:
    items: list[RawItem]
    errors: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    """One configured source (a feed URL, a repo pattern, a web page...)."""

    source_type: ClassVar[str] = "abstract"

    def __init__(self, config: RadarConfig | None = None) -> None:
        self.config = config
        opts: dict = config.options if config else {}
        self.timeout = float(opts.get("fetch_timeout_seconds", 20))
        self.max_items = int(opts.get("max_items_per_feed", 40))

    @abstractmethod
    def fetch(self) -> FetchResult: ...


def strip_tags(html_fragment: str) -> str:
    """Very small tag stripper for RSS description blobs (data cleaning only)."""
    text = _TAG_RE.sub(" ", html_fragment)
    text = (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        .replace("&#x27;", "'").replace("&#x2F;", "/")
    )
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
