"""radar — command-line interface.

Commands:
    radar fetch                 fetch + normalize + store (+ enrich) from all sources
    radar report [--out DIR]    render the Markdown report for the lookback window
    radar run                   complete pipeline: fetch → dedup → report
    radar sources               list configured sources & adapters
    radar export-prompts        copy built-in LLM prompts for customization

Global flags: --config PATH (default config/sources.yaml), --version.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_config
from .db import Database
from .utils import parse_env_file

_BUILTIN_PROMPTS = Path(__file__).parent / "prompts"


def _bootstrap(config_path: str) -> tuple[Any, Database]:
    parse_env_file(Path(".env"))  # optional; real env vars always win
    config = load_config(config_path)
    db = Database(config.database_path)
    return config, db


def cmd_fetch(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline

    config, db = _bootstrap(args.config)
    try:
        stats = Pipeline(config, db).fetch_stage()
    finally:
        db.close()
    print(
        f"fetched {stats['fetched']} items from "
        f"{stats['sources_ok']} OK / {stats['sources_failed']} failed sources; "
        f"{stats['inserted']} new"
    )
    for err in stats["errors"][:10]:
        print(f"  ⚠️ {err}", file=sys.stderr)
    if len(stats["errors"]) > 10:
        print(f"  … and {len(stats['errors']) - 10} more warnings", file=sys.stderr)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    from .report import generate_report

    config, db = _bootstrap(args.config)
    try:
        pipeline = Pipeline(config, db)
        path = generate_report(pipeline, out_dir=args.out, filename=args.filename)
    finally:
        db.close()
    print(f"report written: {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    from .report import generate_report

    config, db = _bootstrap(args.config)
    try:
        pipeline = Pipeline(config, db)
        result = pipeline.run()
        path = generate_report(pipeline, out_dir=args.out)
    finally:
        db.close()

    fetch = result["fetch"]
    print(
        f"[fetch] {fetch['fetched']} fetched / {fetch['inserted']} new · "
        f"{fetch['sources_ok']} sources ok, {fetch['sources_failed']} failed"
    )
    print(f"[dedup] {result['dedup']['merged']} duplicates merged "
          f"({result['dedup']['groups']} groups)")
    print(f"[mode ] {result['mode']}")
    print(f"[report] {path}")
    for err in fetch["errors"][:10]:
        print(f"  ⚠️ {err}", file=sys.stderr)
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    from .adapters import available_adapter_sections

    config, _ = _bootstrap(args.config)
    print("topics:")
    for t in config.topics:
        kws = f" (keywords: {', '.join(t.keywords)})" if t.keywords else ""
        print(f"  - {t.label}{kws}")
    print("\ngithub patterns:")
    for p in config.github_patterns:
        print(f"  - {p}")
    print("\nfeeds:")
    for f in config.feeds:
        label = f.name or f.url
        kind = " [youtube]" if f.source_type == "youtube" else ""
        print(f"  - {label}{kind} → {f.url}")
    print("\npages:")
    for p in config.pages:
        print(f"  - {p.name or p.url} → {p.url}")
    sections = ", ".join(sorted(available_adapter_sections()))
    print(f"\nadapters registered: {sections}")
    print(f"database: {config.database_path}")
    print(f"reports : {config.report_dir}/  (lookback "
          f"{config.options.get('lookback_days')} days)")
    return 0


def cmd_export_prompts(args: argparse.Namespace) -> int:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted(_BUILTIN_PROMPTS.glob("*.md")):
        dest = target / src.name
        if dest.exists() and not args.force:
            print(f"skip existing {dest} (--force to overwrite)")
            continue
        shutil.copy(src, dest)
        copied.append(str(dest))
    for c in copied:
        print(f"wrote {c}")
    print("point your config at this dir via llm.prompts_dir")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Personal technology-intelligence radar.",
    )
    parser.add_argument("--config", default="config/sources.yaml",
                        help="path to sources.yaml (default: %(default)s)")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="run all source adapters and store items")
    p_fetch.set_defaults(func=cmd_fetch)

    p_report = sub.add_parser("report", help="render the Markdown report")
    p_report.add_argument("--out", default=None, help="output directory override")
    p_report.add_argument("--filename", default=None,
                          help="report filename override (default YYYY-MM-DD.md)")
    p_report.set_defaults(func=cmd_report)

    p_run = sub.add_parser("run", help="full pipeline: fetch → dedup → report")
    p_run.add_argument("--out", default=None, help="output directory override")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("sources", help="show configured sources").set_defaults(
        func=cmd_sources
    )

    p_prompts = sub.add_parser("export-prompts",
                               help="copy built-in LLM prompts for customization")
    p_prompts.add_argument("--dir", default="prompts")
    p_prompts.add_argument("--force", action="store_true")
    p_prompts.set_defaults(func=cmd_export_prompts)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
