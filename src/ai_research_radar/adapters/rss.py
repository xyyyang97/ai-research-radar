"""RSS 2.0 / Atom feed adapter (stdlib xml.etree, hardened).

Hardening notes (untrusted external XML):

* Billion-laughs / entity-expansion attacks require an internal DTD, so any
  feed whose prolog contains ``<!DOCTYPE`` or ``<!ENTITY`` is rejected outright
  — real-world feeds never need them.
* External entities cannot resolve: ElementTree raises on undefined entities,
  and we never install a custom resolver.
* All extracted text passes through ``strip_tags`` (entity decoding) before it
  reaches the pipeline; content is data, never executed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import net
from ..config import RadarConfig
from ..models import RawItem
from .base import FetchResult, SourceAdapter, strip_tags, truncate

_DTD_RE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)

_YT_ID_RE = re.compile(r"youtu(?:\.be/be|be\.com/watch\?v=)([\w-]{6,})", re.IGNORECASE)


def _local(tag: str) -> str:
    """Strip the XML namespace from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _text_of(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return strip_tags("".join(element.itertext()))


def _findall(root: ET.Element, name: str) -> list[ET.Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def _find(root: ET.Element, name: str) -> ET.Element | None:
    for el in root.iter():
        if _local(el.tag) == name:
            return el
    return None


class FeedAdapter(SourceAdapter):
    """Fetches one RSS/Atom URL (including YouTube channel feeds)."""

    source_type = "rss"

    def __init__(self, url: str, name: str = "",
                 source_type: str = "rss",
                 config: RadarConfig | None = None) -> None:
        super().__init__(config)
        self.url = url
        self.name_override = name or ""
        self.kind = source_type  # "rss" or "youtube"

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def _parse_entries(xml_text: str) -> tuple[str, list[dict[str, str]]]:
        root = ET.fromstring(xml_text)
        entries: list[dict[str, str]] = []

        if _local(root.tag) == "rss":  # RSS 2.0
            channel = _find(root, "channel")
            feed_title = _text_of(_find(channel, "title")) if channel is not None else ""
            for item_el in _findall(root, "item"):
                link_el = _find(item_el, "link")
                link = (link_el.text or "").strip() if link_el is not None else ""
                if not link:
                    guid_el = _find(item_el, "guid")
                    link = (guid_el.text or "").strip() if guid_el is not None else ""
                entries.append(
                    {
                        "title": _text_of(_find(item_el, "title")),
                        "url": link,
                        "published": _text_of(_find(item_el, "pubDate")),
                        "author": _text_of(_find(item_el, "author"))
                        or _text_of(_find(item_el, "creator")),
                        "content": _text_of(_find(item_el, "description")),
                    }
                )
        else:  # Atom
            feed_title = ""
            for child in root:
                if _local(child.tag) == "title":
                    feed_title = _text_of(child)
                    break
            for entry in _findall(root, "entry"):
                link = ""
                for link_el in [e for e in entry if _local(e.tag) == "link"]:
                    rel = link_el.get("rel", "alternate")
                    href = link_el.get("href", "")
                    if rel == "alternate" and href:
                        link = href
                        break
                    link = link or href
                published = _text_of(_find(entry, "published")) or _text_of(
                    _find(entry, "updated")
                )
                author_el = _find(entry, "author")
                author = _text_of(_find(author_el, "name")) if author_el is not None else ""
                content = (
                    _text_of(_find(entry, "content"))
                    or _text_of(_find(entry, "summary"))
                )
                entries.append(
                    {
                        "title": _text_of(_find(entry, "title")),
                        "url": link.strip(),
                        "published": published,
                        "author": author,
                        "content": content,
                    }
                )
        return feed_title, entries

    # -- fetching --------------------------------------------------------------

    def fetch(self) -> FetchResult:
        errors: list[str] = []
        items: list[RawItem] = []
        try:
            resp = net.fetch(self.url, timeout=self.timeout)
        except RuntimeError as exc:
            return FetchResult(items=[], errors=[f"{self.url}: {exc}"])

        body = resp.text
        if _DTD_RE.search(body[:2048]):
            return FetchResult(
                items=[],
                errors=[f"{self.url}: rejected (DOCTYPE/ENTITY in XML)"],
            )

        try:
            feed_title, entries = self._parse_entries(body)
        except ET.ParseError as exc:
            return FetchResult(items=[], errors=[f"{self.url}: unparseable XML ({exc})"])

        source_name = self.name_override or feed_title or self.url
        for entry in entries[: self.max_items]:
            url = entry["url"]
            title = entry["title"].strip()
            if not url or not title:
                continue
            if self.kind == "youtube":
                m = _YT_ID_RE.search(url)
                url = f"https://www.youtube.com/watch?v={m.group(1)}" if m else url
            published = entry["published"]
            iso = _safe_date(published)
            items.append(
                RawItem(
                    source=source_name,
                    source_type=self.kind,
                    title=title[:500],
                    url=url,
                    published_at=iso,
                    author=entry["author"][:200],
                    raw_content=truncate(entry["content"]),
                )
            )
        return FetchResult(items=items, errors=errors)


_RFC822_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _safe_date(raw: str) -> str:
    """Best-effort RFC-822 / ISO date → 'YYYY-MM-DDTHH:MM:SSZ'; '' on failure.

    Deliberately regex-based rather than email.utils.parsedate_to_datetime so a
    malformed date string from an external feed can never raise deep inside the
    parser machinery.
    """
    raw = raw.strip()
    if not raw:
        return ""
    m = re.search(
        r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})"
        r"(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?",
        raw,
    )
    if m:
        day, mon, year = m.group(1).zfill(2), m.group(2).lower(), m.group(3)
        month = _RFC822_MONTHS.get(mon)
        if month:
            hh, mm, ss = m.group(4) or "00", m.group(5) or "00", m.group(6) or "00"
            return f"{year}-{month}-{day}T{hh}:{mm}:{ss}Z"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1) + "T00:00:00Z"
    return ""
