"""Shared fixtures: temp config, temp DB, sample items."""

from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_research_radar.config import RadarConfig, Topic  # noqa: E402
from ai_research_radar.db import Database  # noqa: E402
from ai_research_radar.models import RawItem  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def config(tmp_path: Path) -> RadarConfig:
    return RadarConfig(
        topics=[
            Topic(name="openai", label="OpenAI", keywords=["gpt"]),
            Topic(name="acquisitions", label="Acquisitions",
                  keywords=["acquisition", "acquires"]),
            Topic(name="agents", label="Agents", keywords=["agent runtime"]),
        ],
        github_patterns=["example/*"],
        options={
            "database_path": str(tmp_path / "test.db"),
            "report_dir": str(tmp_path / "reports"),
            "lookback_days": 7,
            "dedup_similarity_threshold": 6,
            "fetch_timeout_seconds": 5,
        },
    )


@pytest.fixture()
def db(config: RadarConfig) -> Generator[Database, None, None]:
    database = Database(config.database_path)
    yield database
    database.close()


@pytest.fixture()
def item_factory() -> Callable[..., RawItem]:
    def make(**kw: Any) -> RawItem:
        defaults = {
            "source": "Test Source",
            "source_type": "rss",
            "title": "A test title",
            "url": "https://example.com/test",
            "published_at": "2026-08-25T10:00:00Z",
            "raw_content": "Some body text for the test item.",
        }
        defaults.update(kw)
        return RawItem(**defaults)

    return make
