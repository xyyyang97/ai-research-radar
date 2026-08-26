"""Small shared utilities."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE lines, # comments, optional quotes).

    Never overrides variables already present in the environment.
    """
    env: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            env[key] = value
            os.environ[key] = value
    return env


def days_ago_iso(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def first_sentences(text: str, limit_chars: int = 240) -> str:
    """Extractive summary helper: leading complete sentences within budget."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    sentences = _SENTENCE_RE.split(text)
    out = ""
    for s in sentences:
        candidate = f"{out} {s}".strip()
        if len(candidate) > limit_chars and out:
            break
        out = candidate
        if len(out) >= limit_chars * 0.6:
            break
    if not out:  # no sentence boundary found — hard truncate at word edge
        cut = text[:limit_chars]
        out = cut.rsplit(" ", 1)[0]
    return out
