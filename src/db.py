"""SQLite state: what has been seen/analyzed/briefed, plus a log of runs."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .models import IntelItem
from .util import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, source_id TEXT, title TEXT, link TEXT, summary TEXT, published TEXT,
    first_seen TEXT, score REAL, status TEXT, run_id INTEGER, matched_terms TEXT
);
CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, item_id TEXT, kind TEXT,
    headline TEXT, created_at TEXT, brief_file TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT, dry_run INTEGER,
    duration_s REAL, sources_ok INTEGER, sources_failed INTEGER, collected INTEGER, after_lookback INTEGER,
    duplicates_removed INTEGER, below_min_score INTEGER, sent_to_llm INTEGER, act_count INTEGER,
    signal_count INTEGER, brief_file TEXT, llm_engine TEXT, error TEXT, source_stats TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen);
"""
RUN_COLUMNS = {
    "duration_s", "sources_ok", "sources_failed", "collected", "after_lookback", "duplicates_removed",
    "below_min_score", "sent_to_llm", "act_count", "signal_count", "brief_file", "llm_engine", "error",
}


class Database:
    def __init__(self, path: Path, readonly: bool = False):
        self.readonly = readonly
        if readonly and not Path(path).exists():
            self.conn = sqlite3.connect(":memory:")  # dry-run on a fresh install: empty history
        elif readonly:
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        if not readonly or not Path(path).exists():
            self.conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was created."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(items)")}
        if "matched_terms" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN matched_terms TEXT")
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # maintenance -----------------------------------------------------------
    def backup(self, dest_dir: Path, keep: int) -> Path:
        """Consistent copy via SQLite's online backup API; keeps the newest `keep` intel_*.db files."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"intel_{utcnow():%Y%m%d_%H%M%S}.db"
        target = sqlite3.connect(dest)
        try:
            self.conn.backup(target)
        finally:
            target.close()
        for old in sorted(dest_dir.glob("intel_*.db"))[:-max(keep, 1)]:
            old.unlink()
        return dest

    def prune(self, days: int) -> int:
        """Delete items (and their briefings) first seen more than `days` ago. Returns items deleted."""
        cutoff = (utcnow() - timedelta(days=days)).isoformat()
        self.conn.execute("DELETE FROM briefings WHERE item_id IN (SELECT id FROM items WHERE first_seen < ?)",
                          (cutoff,))
        n = self.conn.execute("DELETE FROM items WHERE first_seen < ?", (cutoff,)).rowcount
        self.conn.commit()
        return n

    def recent_source_stats(self, runs: int) -> list[dict]:
        """Per-source stats of the last `runs` finished real runs, newest first."""
        rows = self.conn.execute(
            "SELECT source_stats FROM runs WHERE dry_run = 0 AND source_stats IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (runs,)).fetchall()
        return [json.loads(r["source_stats"]) for r in rows]

    # runs ------------------------------------------------------------------
    def start_run(self, dry_run: bool) -> int:
        cur = self.conn.execute("INSERT INTO runs (started_at, status, dry_run) VALUES (?, 'running', ?)",
                                (utcnow().isoformat(), int(dry_run)))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, metrics: dict, source_stats: dict) -> None:
        cols = {k: v for k, v in metrics.items() if k in RUN_COLUMNS}
        sets = ", ".join(f"{k}=?" for k in cols)
        self.conn.execute(
            f"UPDATE runs SET finished_at=?, status=?, source_stats=?{', ' + sets if sets else ''} WHERE id=?",
            [utcnow().isoformat(), status, json.dumps(source_stats), *cols.values(), run_id],
        )
        self.conn.commit()

    # items -------------------------------------------------------------------
    def existing_ids(self, ids: list[str]) -> set[str]:
        found: set[str] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT id FROM items WHERE id IN ({','.join('?' * len(chunk))})"
            found.update(r["id"] for r in self.conn.execute(q, chunk))
        return found

    def recent_items(self, days: int) -> list[dict]:
        """Recent history with the latest briefing (if any) for each item."""
        cutoff_iso = (utcnow() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            """SELECT i.id, i.title, i.status, b.headline, substr(b.created_at, 1, 10) AS brief_date
               FROM items i LEFT JOIN briefings b ON b.item_id = i.id
                    AND b.id = (SELECT MAX(id) FROM briefings WHERE item_id = i.id)
               WHERE i.first_seen >= ?""", (cutoff_iso,)).fetchall()
        return [dict(r) for r in rows]

    def search_items(self, days: float, *, statuses: list[str] | None = None, sources: list[str] | None = None,
                     text: str | None = None, limit: int = 200) -> list[dict]:
        """Past items for review, newest first, with the latest briefing (if any) for each."""
        where, args = ["i.first_seen >= ?"], [(utcnow() - timedelta(days=days)).isoformat()]
        if statuses:
            where.append(f"i.status IN ({','.join('?' * len(statuses))})")
            args += statuses
        if sources:
            where.append(f"i.source_id IN ({','.join('?' * len(sources))})")
            args += sources
        if text:
            where.append("(i.title LIKE ? OR i.summary LIKE ? OR b.headline LIKE ?)")
            args += [f"%{text}%"] * 3
        rows = self.conn.execute(
            f"""SELECT i.*, b.kind, b.headline, b.created_at AS brief_date, b.brief_file
                FROM items i LEFT JOIN briefings b ON b.item_id = i.id
                     AND b.id = (SELECT MAX(id) FROM briefings WHERE item_id = i.id)
                WHERE {' AND '.join(where)}
                ORDER BY i.first_seen DESC, i.score DESC LIMIT ?""", [*args, limit]).fetchall()
        return [dict(r) for r in rows]

    def save_items(self, items: list[IntelItem], statuses: dict[str, str], run_id: int) -> None:
        now = utcnow().isoformat()
        self.conn.executemany(
            """INSERT OR REPLACE INTO items (id, source_id, title, link, summary, published, first_seen, score, status,
                                            run_id, matched_terms)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT first_seen FROM items WHERE id = ?), ?), ?, ?, ?, ?)""",
            [(i.id, i.source_id, i.title, i.link, i.summary, i.published.isoformat() if i.published else None,
              i.id, now, i.score, statuses.get(i.id, "reviewed"), run_id, json.dumps(i.matched_terms))
             for i in items],
        )
        self.conn.commit()

    def save_briefings(self, rows: list[tuple[str, str, str]], run_id: int, brief_file: str) -> None:
        now = utcnow().isoformat()
        self.conn.executemany(
            "INSERT INTO briefings (run_id, item_id, kind, headline, created_at, brief_file) VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, item_id, kind, headline, now, brief_file) for item_id, kind, headline in rows],
        )
        self.conn.commit()
