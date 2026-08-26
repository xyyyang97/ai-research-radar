"""CLI tests via main(argv) — no subprocess, no network."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_research_radar.cli import main


def _config(tmp_path: Path) -> Path:
    cfg = {
        "database_path": str(tmp_path / "cli.db"),
        "report_dir": str(tmp_path / "reports"),
        "topics": ["openai"],
        "github": ["example/*"],
        "feeds": [],
        "pages": [],
        "options": {"lookback_days": 7},
    }
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


def test_version_flag(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "radar" in capsys.readouterr().out


def test_sources_command_lists_config(capsys, tmp_path):
    cfg = _config(tmp_path)
    assert main(["--config", str(cfg), "sources"]) == 0
    out = capsys.readouterr().out
    assert "openai" in out
    assert "example/*" in out
    assert "adapters registered" in out


def test_missing_config_errors_cleanly(capsys, tmp_path):
    rc = main(["--config", str(tmp_path / "nope.yaml"), "sources"])
    assert rc != 0
    assert "error" in capsys.readouterr().err.lower()


def test_export_prompts_copies_files(tmp_path, capsys):
    target = tmp_path / "prompts"
    assert main(["export-prompts", "--dir", str(target)]) == 0
    assert (target / "summarization.md").exists()
    assert (target / "classification.md").exists()
    # second run without --force skips
    assert main(["export-prompts", "--dir", str(target)]) == 0
    assert "skip existing" in capsys.readouterr().out
