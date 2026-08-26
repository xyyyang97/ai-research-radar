"""Adapter registry — maps config sections to adapter classes.

Extension point: call ``register_adapter(section_name, factory)`` before
``build_adapters`` to add a brand-new source type (e.g. Telegram) without
touching the pipeline. The factory receives ``(entry, config)`` where *entry*
is whatever the YAML section contains for that source.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from typing import Any

from ..config import RadarConfig
from .base import SourceAdapter
from .github import GitHubAdapter
from .page import PageAdapter
from .rss import FeedAdapter

# section -> factory(entry, config) -> SourceAdapter
_REGISTRY: dict[str, Callable[[Any, RadarConfig], SourceAdapter]] = {}


def register_adapter(section: str,
                     factory: Callable[[Any, RadarConfig], SourceAdapter]) -> None:
    _REGISTRY[section] = factory


def _feed_factory(entry: Any, config: RadarConfig) -> FeedAdapter:
    if isinstance(entry, str):
        url, name, stype = entry, "", "rss"
    else:
        url = str(entry.get("url", ""))
        name = str(entry.get("name", ""))
        stype = str(entry.get("type", "rss"))
    kind = "youtube" if _is_youtube(url) else stype
    return FeedAdapter(url=url, name=name, source_type=kind, config=config)


def _is_youtube(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def _github_factory(entry: Any, config: RadarConfig) -> GitHubAdapter:
    patterns = list(entry) if isinstance(entry, (list, tuple)) else [str(entry)]
    return GitHubAdapter(patterns=[str(p) for p in patterns], config=config)


def _page_factory(entry: Any, config: RadarConfig) -> PageAdapter:
    if isinstance(entry, str):
        return PageAdapter(url=entry, config=config)
    return PageAdapter(
        url=str(entry.get("url", "")),
        name=str(entry.get("name", "")),
        link_selector=str(entry.get("link_selector", "")),
        config=config,
    )


def available_adapter_sections() -> list[str]:
    """Names of all registered adapter sections (for `radar sources`)."""
    return sorted(set(_REGISTRY) | {"github"})


register_adapter("feeds", _feed_factory)
register_adapter("github", _github_factory)
register_adapter("pages", _page_factory)


def build_adapters(config: RadarConfig) -> list[SourceAdapter]:
    """Instantiate every configured source adapter."""
    adapters: list[SourceAdapter] = []
    adapters.append(_github_factory(config.github_patterns, config))
    for feed in config.feeds:
        adapters.append(
            _feed_factory({"url": feed.url, "name": feed.name, "type": feed.source_type},
                          config)
        )
    for page in config.pages:
        adapters.append(
            _page_factory({"url": page.url, "name": page.name,
                           "link_selector": page.link_selector}, config)
        )
    return adapters
