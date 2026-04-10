# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Lightweight FTS5 search index for SessionLog entries.

SessionLog remains the primary storage (JSONL, append-only).
SessionIndex is a read-accelerator: dual-write on append, query on search.
Supports cross-session full-text search via SQLite FTS5.
"""
import random
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional


class SessionIndex:
    """SQLite FTS5 index for cross-session message search.

    Uses WAL mode for concurrent reader/writer access and jitter retry
    to avoid write contention (pattern borrowed from hermes-agent).

    Usage:
        index = SessionIndex("/path/to/index.db")
        index.index_message("session_123", "user", "How do I deploy?")
        results = index.search("deploy")
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._write_count = 0
        self._init_schema()

    def _init_schema(self):
        """Create tables and FTS5 virtual table if they don't exist."""
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            content=messages,
            content_rowid=id
        );

        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
        END;
        """)
        self._conn.commit()

    def index_message(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: Optional[float] = None,
    ) -> None:
        """Index a message (called alongside SessionLog.append).

        Args:
            session_id: Session identifier.
            role: Message role ("user", "assistant", "tool", "event").
            content: Message content text.
            timestamp: Unix timestamp. Defaults to current time.
        """
        ts = timestamp or time.time()
        self._execute_write(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_id, role, content, ts),
        )

    def search(
        self,
        query: str,
        session_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Full-text search across sessions.

        Args:
            query: FTS5 query string (supports phrases, boolean, proximity).
            session_id: Optional filter to search within a single session.
            limit: Maximum number of results.

        Returns:
            List of dicts with keys: id, session_id, role, content, timestamp.
        """
        if session_id:
            rows = self._conn.execute(
                """SELECT m.id, m.session_id, m.role, m.content, m.timestamp
                   FROM messages_fts f
                   JOIN messages m ON f.rowid = m.id
                   WHERE f.content MATCH ? AND m.session_id = ?
                   ORDER BY rank LIMIT ?""",
                (query, session_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT m.id, m.session_id, m.role, m.content, m.timestamp
                   FROM messages_fts f
                   JOIN messages m ON f.rowid = m.id
                   WHERE f.content MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "timestamp": r[4],
            }
            for r in rows
        ]

    def _execute_write(self, sql: str, params: tuple) -> None:
        """Execute a write with jitter retry on lock contention.

        Uses random backoff between 20-150ms to avoid thundering herd,
        and periodic WAL checkpoint every 50 writes.
        """
        for attempt in range(15):
            try:
                self._conn.execute(sql, params)
                self._conn.commit()
                self._write_count += 1
                if self._write_count % 50 == 0:
                    try:
                        self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    except Exception:
                        pass  # Checkpoint failure is non-fatal
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 14:
                    time.sleep(random.uniform(0.020, 0.150))
                    continue
                raise

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> "SessionIndex":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
