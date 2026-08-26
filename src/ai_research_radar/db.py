"""SQLite persistence — deliberately boring, zero-infrastructure local state.

The database is a single file (default ``data/radar.db``) holding every fetched
item plus a small key/value table for run bookkeeping. WAL mode keeps concurrent
CLI invocations safe.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fingerprint import content_fingerprint, simhash64, title_key, url_hash
from .models import ITEM_COLUMNS, RawItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT UNIQUE,
    url_hash TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    published_at TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    raw_content TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    topics TEXT NOT NULL DEFAULT '',
    importance_score INTEGER NOT NULL DEFAULT 0,
    reason_for_score TEXT NOT NULL DEFAULT '',
    title_key TEXT NOT NULL DEFAULT '',
    content_simhash INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_titlekey ON items(title_key);
CREATE INDEX IF NOT EXISTS idx_items_simhash ON items(content_simhash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_urlhash
    ON items(url_hash) WHERE url_hash != '';
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA_VERSION = 2


class Database:
    """Thin typed wrapper over the SQLite store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()
        self._conn.commit()

    def _ensure_schema(self) -> None:
        """Create the schema; rebuild (with backup) on version mismatch."""
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version and version != SCHEMA_VERSION:
            backup = self.path.with_suffix(
                f".v{version}.bak{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
            )
            self._conn.close()
            shutil.copy2(self.path, backup)
            self.path.unlink(missing_ok=True)
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        elif not version:
            # fresh database: stamp current version before creating tables
            pass
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- writes -------------------------------------------------------------

    def insert_item(self, item: RawItem, *, status: str = "new") -> int | None:
        """Insert an item. Returns the new row id, or None on duplicate.

        The fingerprint is derived here so adapters cannot forget it.
        """
        item.validate()
        fp = item.fingerprint or content_fingerprint(item.title, item.url, item.raw_content)
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO items ({cols}) VALUES ({ph})".format(
                cols=", ".join(ITEM_COLUMNS[1:]),
                ph=", ".join(["?"] * (len(ITEM_COLUMNS) - 1)),
            ),
            (
                fp,
                url_hash(item.url),
                item.title,
                item.url,
                item.source,
                item.source_type,
                item.published_at or utc_now_iso(),
                item.author,
                item.raw_content,
                item.summary,
                ",".join(item.topics),
                item.importance_score,
                item.reason_for_score,
                title_key(item.title),
                simhash64(f"{item.title}\n{item.raw_content[:2000]}"),
                utc_now_iso(),
                status,
            ),
        )
        self._conn.commit()
        if cur.lastrowid and cur.rowcount > 0:
            return int(cur.lastrowid)
        return None

    def update_item(self, item_id: int, **fields: Any) -> None:
        allowed = {"summary", "topics", "importance_score", "reason_for_score", "status"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update columns: {sorted(unknown)}")
        if "topics" in fields and isinstance(fields["topics"], list):
            fields["topics"] = ",".join(fields["topics"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE items SET {sets} WHERE id = ?",
            [*fields.values(), item_id],
        )
        self._conn.commit()

    def set_meta(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload),
        )
        self._conn.commit()

    # -- reads ---------------------------------------------------------------

    def get_meta(self, key: str) -> Any:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    def known_fingerprints(self, newer_than: str = "") -> set[str]:
        sql = "SELECT fingerprint FROM items"
        params: tuple[Any, ...] = ()
        if newer_than:
            sql += " WHERE first_seen >= ?"
            params = (newer_than,)
        rows = self._conn.execute(sql, params).fetchall()
        return {row["fingerprint"] for row in rows}

    def all_rows(self, newer_than: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM items"
        params: tuple[Any, ...] = ()
        if newer_than:
            sql += " WHERE published_at >= ? OR first_seen >= ?"
            params = (newer_than, newer_than)
        sql += " ORDER BY importance_score DESC, published_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            topics = data.get("topics") or ""
            data["topics"] = [t for t in topics.split(",") if t]
            out.append(data)
        return out

    def find_by_title_key(self, key: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM items WHERE title_key = ? AND title_key != ''", (key,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
