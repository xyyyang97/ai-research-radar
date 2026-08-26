"""Configuration loading & validation.

The YAML file under ``config/sources.yaml`` is the user's control surface.
Everything downstream (adapters, pipeline, reports) reads from this object;
no module hardcodes sources or topics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class Topic:
    name: str
    label: str = ""
    keywords: list[str] = field(default_factory=list)

    def match_terms(self) -> list[str]:
        base = {self.name.replace("-", " ").lower(), *self.keywords}
        if len(base) == 1:  # plain string topic: also allow the raw token
            base.add(self.name.lower())
        return sorted(base)


@dataclass(slots=True)
class FeedSource:
    url: str
    name: str = ""
    source_type: str = "rss"  # rss | youtube (YouTube channel feeds are Atom too)


@dataclass(slots=True)
class PageSource:
    url: str
    name: str = ""
    link_selector: str = ""


@dataclass(slots=True)
class RadarConfig:
    topics: list[Topic] = field(default_factory=list)
    github_patterns: list[str] = field(default_factory=list)
    feeds: list[FeedSource] = field(default_factory=list)
    pages: list[PageSource] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)

    @property
    def database_path(self) -> Path:
        p = Path(str(self.options.get("database_path", "data/radar.db")))
        return p

    @property
    def report_dir(self) -> Path:
        return Path(str(self.options.get("report_dir", "reports")))


_TOPIC_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _parse_topics(raw: Any) -> list[Topic]:
    topics: list[Topic] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("`topics` must be a list of strings or mappings")
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            name, label, keywords = entry, "", []
        elif isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
            label = str(entry.get("label", ""))
            kw = entry.get("keywords", [])
            if isinstance(kw, str):
                kw = [kw]
            keywords = [str(k) for k in kw]
        else:
            raise ValueError(f"invalid topic entry: {entry!r}")
        if not name or not _TOPIC_NAME_RE.match(name):
            raise ValueError(f"invalid topic name: {name!r} (use lowercase-kebab)")
        if name in seen:
            raise ValueError(f"duplicate topic: {name}")
        seen.add(name)
        topics.append(Topic(name=name, label=label or name, keywords=keywords))
    return topics


def _parse_feeds(raw: Any) -> list[FeedSource]:
    feeds: list[FeedSource] = []
    if not isinstance(raw, list):
        raise ValueError("`feeds` must be a list")
    for entry in raw:
        if isinstance(entry, str):
            feeds.append(FeedSource(url=entry))
        elif isinstance(entry, dict):
            url = str(entry.get("url", "")).strip()
            if not url:
                raise ValueError(f"feed entry missing url: {entry!r}")
            stype = str(entry.get("type", "rss")).lower()
            if stype not in ("rss", "youtube"):
                raise ValueError(f"feed type must be rss|youtube, got {stype!r}")
            feeds.append(FeedSource(url=url, name=str(entry.get("name", "")), source_type=stype))
        else:
            raise ValueError(f"invalid feed entry: {entry!r}")
    return feeds


def _parse_pages(raw: Any) -> list[PageSource]:
    pages: list[PageSource] = []
    if not isinstance(raw, list):
        raise ValueError("`pages` must be a list")
    for entry in raw:
        if isinstance(entry, str):
            pages.append(PageSource(url=entry))
        elif isinstance(entry, dict):
            url = str(entry.get("url", "")).strip()
            if not url:
                raise ValueError(f"page entry missing url: {entry!r}")
            pages.append(
                PageSource(url=url, name=str(entry.get("name", "")),
                           link_selector=str(entry.get("link_selector", "")))
            )
        else:
            raise ValueError(f"invalid page entry: {entry!r}")
    return pages


def _parse_github(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("`github` must be a list of 'owner/repo' patterns")
    patterns: list[str] = []
    for entry in raw:
        pattern = str(entry).strip().lower()
        parts = pattern.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"github pattern must look like owner/repo or owner/*: {entry!r}")
        patterns.append(pattern)
    return patterns


DEFAULT_OPTIONS: dict[str, Any] = {
    "database_path": "data/radar.db",
    "report_dir": "reports",
    "lookback_days": 7,
    "fetch_timeout_seconds": 20,
    "max_items_per_feed": 40,
    "github_max_releases_per_repo": 5,
    "github_include_activity": True,
    "dedup_similarity_threshold": 16,
}


def load_config(path: str | Path) -> RadarConfig:
    """Load and validate a YAML configuration file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")

    options = dict(DEFAULT_OPTIONS)
    options.update({str(k): v for k, v in (raw.get("options") or {}).items()})

    return RadarConfig(
        topics=_parse_topics(raw.get("topics", [])),
        github_patterns=_parse_github(raw.get("github", [])),
        feeds=_parse_feeds(raw.get("feeds", [])),
        pages=_parse_pages(raw.get("pages", [])),
        options=options,
        llm=dict(raw.get("llm") or {}),
    )
