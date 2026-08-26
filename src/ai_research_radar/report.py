"""Markdown report generation.

Output shape:

    # AI Research Radar
    ## Critical
    ## Important
    ## Worth Watching
    ## GitHub Activity
    ## Releases
    ## Sources

Every important item carries title / source / date / summary / why-it-matters /
importance score / URL. Duplicate clusters surface their corroborating
references inline ("Also reported by …").
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .pipeline import Pipeline


def _fmt_date(iso: str) -> str:
    dt = _parse(iso)
    return dt.strftime("%Y-%m-%d") if dt else "unknown date"


def _parse(value: str) -> Any:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _bullet(row: dict[str, Any], pipeline: Pipeline) -> str:
    refs = pipeline.duplicate_references(int(row["id"]))
    ref_note = ""
    if refs:
        shown = ", ".join(f"[{r['source']}]({r['url']})" for r in refs[:3])
        more = f" (+{len(refs) - 3} more)" if len(refs) > 3 else ""
        ref_note = f"\n  - Also reported by: {shown}{more}"
    topics = ", ".join(row.get("topics") or []) or "unclassified"
    lines = [
        f"- **{row['title']}**",
        (
            f"  `{_fmt_date(row['published_at'])}` · "
            f"{row['source']} · score **{row['importance_score']}** · "
            f"{topics}"
        ),
        f"  - Why it matters: {row['reason_for_score']}",
    ]
    if row.get("summary"):
        lines.append(f"  - Summary: {row['summary']}")
    lines.append(f"  - [original]({row['url']}){ref_note}")
    return "\n".join(lines)


def generate_report(
    pipeline: Pipeline,
    out_dir: str | Path | None = None,
    filename: str = "",
) -> Path:
    """Render the Markdown report for the current lookback window."""
    cfg = pipeline.config
    out_path = Path(out_dir or cfg.report_dir) / (filename or f"{_today()}.md")
    rows = pipeline.report_window_rows()

    critical: list[dict[str, Any]] = []
    important: list[dict[str, Any]] = []
    watching: list[dict[str, Any]] = []
    github_activity: list[dict[str, Any]] = []
    releases: list[dict[str, Any]] = []
    sources_counter: dict[str, int] = {}

    for row in rows:
        source_name = str(row["source"])
        sources_counter[source_name] = sources_counter.get(source_name, 0) + 1
        st = row["source_type"]
        if st == "github":
            if str(row["title"]).startswith("[activity]"):
                github_activity.append(row)
            else:
                releases.append(row)
            continue
        score = int(row["importance_score"])
        if score >= 70:
            critical.append(row)
        elif score >= 45:
            important.append(row)
        else:
            watching.append(row)

    lines: list[str] = []
    ap = lines.append

    ap("# AI Research Radar")
    ap("")
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lookback = cfg.options.get("lookback_days", 7)
    mode = pipeline.summarizer.name
    ap(
        f"*Generated {generated} · window: last {lookback} days · "
        f"{len(rows)} items ({mode}) · config: `config/sources.yaml`*"
    )
    ap("")
    ap("> Scores are heuristic (0-100) with per-signal explanations — see the")
    ap("> “why it matters” line under each item. No LLM is required.")
    ap("")

    def section(name: str, items: list[dict[str, Any]], empty_text: str,
                limit: int = 20) -> None:
        ap(f"## {name}")
        ap("")
        if not items:
            ap(f"*{empty_text}*")
            ap("")
            return
        items.sort(key=lambda r: (-int(r["importance_score"]), str(r["published_at"])))
        for row in items[:limit]:
            ap(_bullet(row, pipeline))
            ap("")

    section("Critical", critical, "Nothing critical this window.")
    section("Important", important, "Nothing in the important band this window.")
    section("Worth Watching", watching, "Nothing in this band this window.")

    ap("## GitHub Activity")
    ap("")
    if not github_activity:
        ap("*No notable repository activity this window.*")
        ap("")
    for row in github_activity[:15]:
        repo = str(row["source"]).removeprefix("github:")
        title = str(row["title"]).removeprefix("[activity] ")
        ap(f"- [{repo}]({row['url']}) — {title} `{_fmt_date(row['published_at'])}`")
    ap("")

    ap("## Releases")
    ap("")
    if not releases:
        ap("*No releases this window.*")
        ap("")
    for row in releases[:25]:
        repo = str(row["source"]).removeprefix("github:")
        ap(
            f"- **{row['title']}** — [{repo}]({row['url']})"
            f" `{_fmt_date(row['published_at'])}`"
        )
        if row.get("summary"):
            ap(f"  - {row['summary']}")
    ap("")

    ap("## Sources")
    ap("")
    if not sources_counter:
        ap("*No sources contributed items in this window.*")
    else:
        for src, count in sorted(sources_counter.items(), key=lambda kv: (-kv[1], kv[0])):
            ap(f"- {src} x{count}")
    ap("")

    footer_stats = pipeline.db.get_meta("last_fetch")
    if isinstance(footer_stats, dict):
        errors = footer_stats.get("errors") or []
        if errors:
            ap("---")
            ap("_Fetch warnings:_")
            for err in errors[:10]:
                ap(f"- ⚠️ {err}")
        ap("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
