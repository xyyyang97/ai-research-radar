"""Unit tests: configuration loading & validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_research_radar.config import load_config


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


class TestLoadConfig:
    def test_minimal_string_forms(self, tmp_path):
        path = _write(tmp_path, {
            "topics": ["ai-agents"],
            "github": ["openai/codex"],
            "feeds": ["https://example.com/feed.xml"],
            "pages": ["https://example.com/blog"],
        })
        cfg = load_config(path)
        assert [t.name for t in cfg.topics] == ["ai-agents"]
        assert cfg.github_patterns == ["openai/codex"]
        assert cfg.feeds[0].url.endswith(".xml")
        assert cfg.pages[0].url.endswith("/blog")

    def test_mapping_forms(self, tmp_path):
        path = _write(tmp_path, {
            "topics": [{"name": "llm-infra", "label": "Infra",
                        "keywords": ["vllm"]}],
            "feeds": [{"name": "Named", "url": "https://e.com/f",
                       "type": "youtube"}],
            "pages": [{"name": "P", "url": "https://e.com/b",
                       "link_selector": "a.card"}],
            "options": {"lookback_days": 3},
        })
        cfg = load_config(path)
        assert cfg.topics[0].keywords == ["vllm"]
        assert cfg.feeds[0].source_type == "youtube"
        assert cfg.options["lookback_days"] == 3
        # defaults still present
        assert "max_items_per_feed" in cfg.options

    def test_invalid_topic_name_rejected(self, tmp_path):
        path = _write(tmp_path, {"topics": ["Bad Name!"]})
        with pytest.raises(ValueError):
            load_config(path)

    def test_duplicate_topic_rejected(self, tmp_path):
        path = _write(tmp_path, {"topics": ["x", {"name": "x"}]})
        with pytest.raises(ValueError):
            load_config(path)

    def test_bad_feed_type_rejected(self, tmp_path):
        path = _write(tmp_path, {"feeds": [{"url": "https://e.com", "type": "scrape"}]})
        with pytest.raises(ValueError):
            load_config(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.yaml")

    def test_repo_example_config_loads(self):
        """The shipped example config must stay valid."""
        root = Path(__file__).resolve().parents[1]
        cfg = load_config(root / "config" / "sources.yaml")
        assert len(cfg.topics) >= 5
        assert any("*" in p for p in cfg.github_patterns)
