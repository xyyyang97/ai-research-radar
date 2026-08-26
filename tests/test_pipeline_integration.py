"""Integration tests: full pipeline against a local HTTP server.

No external network required — RADAR_ALLOW_PRIVATE=1 is set for the server
host (127.0.0.1), which is exactly what the flag exists for.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ai_research_radar.adapters.page import PageAdapter
from ai_research_radar.adapters.rss import FeedAdapter
from ai_research_radar.config import RadarConfig, Topic
from ai_research_radar.db import Database
from ai_research_radar.pipeline import Pipeline
from ai_research_radar.report import generate_report
from ai_research_radar.scoring import score_item

FIXTURES = Path(__file__).parent / "fixtures"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        mapping = {
            "/feed-a.xml": "feed_vendor_a.xml",
            "/feed-b.xml": "feed_aggregator_b.xml",
            "/yt.xml": "youtube_atom.xml",
            "/blog": "page_blog.html",
        }
        fname = mapping.get(self.path)
        if fname is None:
            self.send_error(404)
            return
        body = (FIXTURES / fname).read_bytes()
        ctype = ("application/rss+xml" if fname.endswith(".xml")
                 else "text/html; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib API
        pass


@pytest.fixture()
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture()
def pipeline_config(
    tmp_path: Path, local_server: str,
) -> tuple[RadarConfig, str]:
    config = RadarConfig(
        topics=[
            Topic(name="openai", label="OpenAI", keywords=["gpt"]),
            Topic(name="acme", label="Acme", keywords=["acme"]),
        ],
        github_patterns=[],
        options={
            "database_path": str(tmp_path / "int.db"),
            "report_dir": str(tmp_path / "reports"),
            "lookback_days": 7,
            "dedup_similarity_threshold": 16,
            "fetch_timeout_seconds": 5,
            "max_items_per_feed": 40,
        },
    )
    return config, local_server


@pytest.fixture()
def no_llm(monkeypatch):
    monkeypatch.setenv("RADAR_LLM_PROVIDER", "none")


@pytest.fixture()
def allow_private(monkeypatch):
    # The local test HTTP server lives on 127.0.0.1 — exactly what this
    # opt-in flag is documented for.
    monkeypatch.setenv("RADAR_ALLOW_PRIVATE", "1")


class TestEndToEnd:
    def test_fetch_normalize_store(self, pipeline_config, no_llm, allow_private):
        config, base = pipeline_config
        db = Database(config.database_path)
        try:
            adapter = FeedAdapter(url=f"{base}/feed-a.xml", name="Vendor A",
                                  source_type="rss", config=config)
            result = adapter.fetch()
            assert result.errors == []
            assert len(result.items) == 2
            first = result.items[0]
            assert first.title == "Acme launches Model X with new pricing"
            assert first.author == "Jane Doe"
            assert first.published_at == "2026-08-25T10:00:00Z"
            new_id = db.insert_item(first)
            assert new_id is not None
        finally:
            db.close()

    def test_youtube_feed_normalizes_watch_urls(self, pipeline_config, no_llm, allow_private):
        config, base = pipeline_config
        adapter = FeedAdapter(url=f"{base}/yt.xml", name="Channel C",
                              source_type="youtube", config=config)
        result = adapter.fetch()
        assert result.items[0].source_type == "youtube"
        assert result.items[0].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_full_pipeline_dedup_report(self, pipeline_config, no_llm, allow_private):
        """fetch → normalize → dedup → classify → score → summarize → report."""
        config, base = pipeline_config
        db = Database(config.database_path)
        try:
            pipeline = Pipeline(config, db)
            # two feeds carry the same headline (syndication fixture pair)
            for path in ("/feed-a.xml", "/feed-b.xml"):
                res = FeedAdapter(url=f"{base}{path}", source_type="rss",
                                  config=config).fetch()
                for item in res.items:
                    db.insert_item(item)

            rows_before = db.all_rows(newer_than="2000-01-01T00:00:00Z")
            stats = pipeline.dedup_stage()
            assert stats["groups"] >= len(rows_before) - 2

            rows = db.all_rows(newer_than="2000-01-01T00:00:00Z")
            dupes = [r for r in rows if r["status"] == "duplicate"]
            assert len(dupes) == 1  # the syndicated headline pair merged

            # classify + score + summarize every surviving row

            survivors = [r for r in rows if r["status"] != "duplicate"]
            enriched = []
            for row in survivors:
                topics = pipeline.classifier.classify(
                    row["title"], row["raw_content"], row["source"])
                scored = score_item(
                    title=row["title"], content=row["raw_content"],
                    url=row["url"], source_type=row["source_type"],
                    published_at=row["published_at"], topics=topics,
                    config=config)
                db.update_item(row["id"], topics=topics,
                               importance_score=scored.score,
                               reason_for_score=scored.explanation,
                               summary=pipeline.summarizer.summarize(
                                   row["title"], row["raw_content"]))
                enriched.append(row["id"])
            out = generate_report(pipeline, filename="test-report.md")
            text = out.read_text(encoding="utf-8")
            assert "# AI Research Radar" in text
            for section in ("## Critical", "## Important", "## Worth Watching",
                            "## GitHub Activity", "## Releases", "## Sources"):
                assert section in text
            assert "Why it matters:" in text  # explanations present
            assert "Also reported by" in text  # corroborating refs preserved
        finally:
            db.close()

    def test_page_adapter_via_http(self, pipeline_config, no_llm, allow_private):
        config, base = pipeline_config
        adapter = PageAdapter(url=f"{base}/blog", name="Example Blog",
                              config=config)
        result = adapter.fetch()
        titles = [i.title for i in result.items]
        assert any("Acme acquires WidgetCo" in t for t in titles)
        assert all("hiring" not in t.lower() for t in titles)
