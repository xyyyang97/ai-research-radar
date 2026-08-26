"""Live-network tests against real public sources.

Deselected by default (see pyproject markers); run explicitly:

    pytest -m network
"""

from __future__ import annotations

import pytest

from ai_research_radar.adapters.rss import FeedAdapter

pytestmark = pytest.mark.network


class TestLiveSources:
    def test_openai_news_feed_live(self):
        adapter = FeedAdapter(
            url="https://openai.com/news/rss.xml", name="OpenAI News",
            source_type="rss", config=None,
        )
        result = adapter.fetch()
        assert result.errors == [], result.errors
        assert len(result.items) > 0
        item = result.items[0]
        assert item.title
        assert item.url.startswith("https://")

    def test_simon_willison_feed_live(self):
        adapter = FeedAdapter(
            url="https://simonwillison.net/atom/everything/",
            source_type="rss", config=None,
        )
        result = adapter.fetch()
        assert result.errors == []
        assert len(result.items) > 5
