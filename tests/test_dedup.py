"""Unit + integration tests for the dedup engine."""

from __future__ import annotations

from ai_research_radar.db import Database
from ai_research_radar.dedup import find_duplicates
from ai_research_radar.models import RawItem

_LONG_A = (
    "Acme announced Model X today with single sign-on support, audit "
    "logging, team management and a new usage dashboard aimed at enterprise "
    "customers who deploy the model across regulated industries. Pricing "
    "starts at fifty dollars per seat with volume discounts available."
)
# Lightly edited syndication: same sentences, small wording changes.
_LIGHT_EDIT = (
    "Acme announced Model X today with single sign-on support, audit "
    "logging, team management and a new usage dashboard aimed at enterprise "
    "customers deploying the model in regulated industries. Pricing starts "
    "at $50 per seat including volume discounts."
)
_UNRELATED = (
    "The community garden will host a tomato tasting event this Saturday "
    "morning. Volunteers should bring baskets and arrive before eight to help "
    "set up tables under the oak trees near the main entrance."
)


def _row(db: Database, item: RawItem) -> dict:
    new_id = db.insert_item(item)
    assert new_id is not None
    rows = {r["id"]: r for r in db.all_rows()}
    return rows[new_id]


def _item(source: str, stype: str, title: str, url: str,
          content: str = "") -> RawItem:
    return RawItem(
        source=source, source_type=stype, title=title, url=url,
        published_at="2026-08-25T10:00:00Z", raw_content=content,
    )


class TestDedupLayers:
    def test_exact_url_duplicate_rejected_at_insert(self, db):
        i1 = _item("A", "rss", "Title One", "https://x.com/post?utm_source=rss")
        i2 = _item("B", "rss", "Different Title", "https://X.com/post")
        assert db.insert_item(i1) is not None
        assert db.insert_item(i2) is None  # fingerprint collision → ignored

    def test_syndicated_headlines_cluster(self, config, db):
        r1 = _row(db, _item("Vendor Blog", "rss",
                            "Acme launches Model X with new pricing",
                            "https://vendor.example/x", content=_LONG_A))
        r2 = _row(db, _item("Aggregator", "rss",
                            "Acme Launches Model X With New Pricing!",
                            "https://agg.example/copy",
                            content="Syndicated summary of the launch."))
        groups = find_duplicates([r1, r2], threshold=16)
        merged = [g for g in groups if len(g.member_ids) > 1]
        assert len(merged) == 1
        g = merged[0]
        # richest copy (longer body) must be primary; other becomes reference
        richest = r1 if len(r1["raw_content"]) >= len(r2["raw_content"]) else r2
        other = r2 if richest is r1 else r1
        assert g.primary_id == richest["id"]
        assert other["id"] in g.member_ids
        assert any(ref["url"] == other["url"] for ref in g.references)

    def test_lightly_edited_same_story_clusters(self, config, db):
        """Simhash layer targets lightly-edited syndication, not deep rewrites.

        Deep paraphrases (word overlap < 0.75) are intentionally kept as
        separate items — merging those needs semantic similarity, which is an
        LLM-tier feature; see docs/extending.md.
        """
        r1 = _row(db, _item("A", "rss", "Model X ships today",
                            "https://a.example/1", content=_LONG_A))
        r2 = _row(db, _item("B", "rss", "Model X ships",
                            "https://b.example/2", content=_LIGHT_EDIT))
        groups = find_duplicates([r1, r2], threshold=16)
        assert any(len(g.member_ids) > 1 for g in groups)

    def test_unrelated_items_never_merge(self, config, db):
        r1 = _row(db, _item("A", "rss", "Launch news",
                            "https://a.example/1", content=_LONG_A))
        r2 = _row(db, _item("B", "rss", "Garden day",
                            "https://b.example/2", content=_UNRELATED))
        groups = find_duplicates([r1, r2], threshold=16)
        assert all(len(g.member_ids) == 1 for g in groups)

    def test_short_github_release_stubs_not_merged(self, config, db):
        """v0.21.0 vs v0.21.1 style stubs must stay separate items."""
        r1 = _row(db, _item("github:o/r", "github", "o/r release v0.21.0: v0.21.0",
                            "https://github.com/o/r/releases/tag/v0.21.0"))
        r2 = _row(db, _item("github:o/r", "github", "o/r release v0.21.1: v0.21.1",
                            "https://github.com/o/r/releases/tag/v0.21.1"))
        groups = find_duplicates([r1, r2], threshold=16)
        assert all(len(g.member_ids) == 1 for g in groups)

    def test_cross_repo_release_stub_never_merges(self, config, db):
        """Regression: python-sdk v0.124.0 vs typescript-sdk v0.119.0 hashed
        nearly identically and were wrongly merged — different repos are
        different announcements regardless of hash distance."""
        r1 = _row(db, _item("github:a/p", "github",
                            "a/anthropic-sdk-python release v0.124.0: v0.124.0",
                            "https://github.com/a/anthropic-sdk-python/releases/tag/v0.124.0"))
        r2 = _row(db, _item("github:a/t", "github",
                            "a/anthropic-sdk-typescript release sdk-v0.119.0",
                            "https://github.com/a/anthropic-sdk-typescript/releases/tag/sdk-v0.119.0"))
        groups = find_duplicates([r1, r2], threshold=16)
        assert all(len(g.member_ids) == 1 for g in groups)

    def test_activity_rows_of_same_repo_distinct_from_releases(self, config, db):
        r1 = _row(db, _item("github:o/r", "github", "[activity] o/r: 5 pushes",
                            "https://github.com/o/r/commits",
                            content="Repository activity signal: 5 pushes."))
        r2 = _row(db, _item("github:o/r", "github", "o/r release v1.2.3: v1.2.3",
                            "https://github.com/o/r/releases/tag/v1.2.3"))
        groups = find_duplicates([r1, r2], threshold=16)
        assert all(len(g.member_ids) == 1 for g in groups)
