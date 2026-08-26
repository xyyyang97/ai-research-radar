"""Pipeline orchestration.

    Sources → Fetch → Normalize → Store → Deduplicate → Classify → Score → Summarize

Every stage is independently runnable and testable; ``Pipeline.run()`` simply
executes them in order and returns a stats dict for CLI display.
"""

from __future__ import annotations

from typing import Any

from .adapters import build_adapters
from .classify import CompositeClassifier
from .config import RadarConfig
from .db import Database
from .dedup import find_duplicates
from .models import RawItem
from .providers import create_provider
from .scoring import score_item
from .summarize import build_summarizer
from .utils import days_ago_iso


class Pipeline:
    def __init__(self, config: RadarConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self.llm = create_provider(config.llm)
        self.summarizer = build_summarizer(self.llm)
        self.classifier = CompositeClassifier(config, self.llm)

    # -- stages -----------------------------------------------------------------

    def fetch_stage(self) -> dict[str, Any]:
        """Run every adapter, normalize and persist new items."""
        stats: dict[str, Any] = {
            "sources_ok": 0,
            "sources_failed": 0,
            "fetched": 0,
            "inserted": 0,
            "errors": [],
        }
        for adapter in build_adapters(self.config):
            label = getattr(adapter, "url", None) or ",".join(
                getattr(adapter, "patterns", [])
            )
            try:
                result = adapter.fetch()
            except Exception as exc:
                stats["sources_failed"] += 1
                stats["errors"].append(f"{label}: adapter crashed: {exc}")
                continue
            stats["fetched"] += len(result.items)
            stats["errors"].extend(f"{label}: {e}" for e in result.errors)
            if result.errors:
                stats["sources_failed"] += 1
            else:
                stats["sources_ok"] += 1
            for item in result.items:
                try:
                    new_id = self.db.insert_item(item)
                    if new_id is not None:
                        stats["inserted"] += 1
                        self._enrich_row(new_id, item)
                except ValueError as exc:  # invalid row — logged, skipped
                    stats["errors"].append(f"{label}: bad item skipped ({exc})")
        self.db.set_meta(
            "last_fetch",
            {"inserted": stats["inserted"], "fetched": stats["fetched"],
             "errors": stats["errors"][:20]},
        )
        return stats

    def _enrich_row(self, item_id: int, item: RawItem) -> None:
        """Classify + score + summarize one freshly inserted item."""
        topics = self.classifier.classify(item.title, item.raw_content, item.source)
        result = score_item(
            title=item.title,
            content=item.raw_content,
            url=item.url,
            source_type=item.source_type,
            published_at=item.published_at,
            topics=topics,
            config=self.config,
        )
        summary = self.summarizer.summarize(item.title, item.raw_content)
        self.db.update_item(
            item_id,
            topics=topics,
            importance_score=result.score,
            reason_for_score=result.explanation,
            summary=summary,
        )

    def dedup_stage(self) -> dict[str, Any]:
        """Mark near-duplicates inside the lookback window."""
        lookback = float(self.config.options.get("lookback_days", 7))
        threshold = int(self.config.options.get("dedup_similarity_threshold", 6))
        rows = self.db.all_rows(newer_than=days_ago_iso(lookback))
        groups = find_duplicates(rows, threshold)
        merged = 0
        for group in groups:
            for member_id in group.member_ids:
                if member_id != group.primary_id:
                    self.db.update_item(member_id, status="duplicate")
                    merged += 1
            if len(group.member_ids) > 1:
                self.db.set_meta(
                    f"dupgroup:{group.primary_id}",
                    {"references": group.references},
                )
        self.db.set_meta("last_dedup", {"groups": len(groups), "merged": merged})
        return {"groups": len(groups), "merged": merged}

    def report_window_rows(self) -> list[dict[str, Any]]:
        lookback = float(self.config.options.get("lookback_days", 7))
        return [
            row
            for row in self.db.all_rows(newer_than=days_ago_iso(lookback))
            if row.get("status") != "duplicate"
        ]

    def duplicate_references(self, row_id: int) -> list[dict[str, str]]:
        data = self.db.get_meta(f"dupgroup:{row_id}")
        if isinstance(data, dict):
            refs = data.get("references", [])
            return [r for r in refs if isinstance(r, dict)]
        return []

    # -- full run ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        fetch_stats = self.fetch_stage()
        dedup_stats = self.dedup_stage()
        mode = self.summarizer.name
        return {
            "fetch": fetch_stats,
            "dedup": dedup_stats,
            "mode": mode,
        }
