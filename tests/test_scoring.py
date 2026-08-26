"""Explainable scoring tests — every score must come with reasons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_research_radar.scoring import score_item, tier_for


def _iso(days_ago: float = 0.0) -> str:
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestScoreSignals:
    def test_fresh_official_launch_scores_high(self, config):
        r = score_item(
            title="OpenAI launches GPT-6 with new API pricing",
            content="The model is now available. Price per token drops.",
            url="https://openai.com/index/gpt-6",
            source_type="rss",
            published_at=_iso(0),
            topics=["openai"],
            config=config,
        )
        text = r.explanation
        assert 60 <= r.score <= 100
        assert "official/vendor channel (+14)" in text
        assert "major product launch" in text
        assert "model release" in text
        assert "pricing change" in text

    def test_old_thirdparty_mundane_item_scores_low(self, config):
        r = score_item(
            title="Weekly changelog roundup",
            content="Dependency bumps and docs fixes.",
            url="https://random-blog.example/weekly",
            source_type="rss",
            published_at=_iso(20),   # outside lookback window
            topics=[],
            config=config,
        )
        assert r.score <= 30
        assert "third-party feed" in r.explanation

    def test_github_authority_and_activity(self, config):
        r = score_item(
            title="o/r release v2.0.0: breaking change",
            url="https://github.com/o/r/releases/tag/v2.0.0",
            source_type="github",
            published_at=_iso(1),
            topics=["openai"],
            github_activity_count=8,
            config=config,
        )
        assert "official GitHub repository (+10)" in r.explanation
        assert "repository activity (8 events" in r.explanation
        assert "breaking/security" in r.explanation

    def test_every_reason_has_points(self, config):
        r = score_item(
            title="Anthropic raises $10B Series G funding round",
            content="The acquisition market heats up as valuation soars.",
            url="https://anthropic.com/news/funding",
            source_type="rss",
            published_at=_iso(0.5),
            topics=["anthropic"],
            config=config,
        )
        for reason in r.reasons:
            assert "(+" in reason, reason

    def test_tier_boundaries(self):
        assert tier_for(70) == "Critical"
        assert tier_for(69) == "Important"
        assert tier_for(45) == "Important"
        assert tier_for(44) == "Worth Watching"

    def test_score_clamped_to_100(self, config):
        r = score_item(
            title="BREAKING: OpenAI launches GPT-9 API with new pricing, "
                  "acquires everyone, tops every benchmark, security advisory CVE-2026-1234",
            content="launch model api price raises benchmark sota breaking change " * 20,
            url="https://openai.com/news/big",
            source_type="rss",
            published_at=_iso(0),
            topics=["openai", "agents"],
            github_activity_count=50,
            config=config,
        )
        assert r.score <= 100
