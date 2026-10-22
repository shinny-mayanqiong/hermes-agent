"""Profile-scoped SQLite bindings for Slack threads and Codex threads."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class Binding:
    team_id: str
    channel_id: str
    thread_ts: str
    user_id: str
    codex_thread_id: str
    cwd: str
    created_at: float
    last_used_at: float


class BindingConflict(RuntimeError):
    """Raised when either side of the one-to-one mapping is already bound."""


class BindingStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO schema_meta(key, value)
            VALUES ('schema_version', '1');

            CREATE TABLE IF NOT EXISTS bindings (
                team_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_ts TEXT NOT NULL,
                user_id TEXT NOT NULL,
                codex_thread_id TEXT NOT NULL UNIQUE,
                cwd TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                PRIMARY KEY (team_id, channel_id, thread_ts)
            );
            CREATE INDEX IF NOT EXISTS idx_codex_slack_last_used
            ON bindings(last_used_at DESC);

            CREATE TABLE IF NOT EXISTS progress (
                codex_thread_id TEXT NOT NULL REFERENCES bindings(codex_thread_id) ON DELETE CASCADE,
                turn_id TEXT NOT NULL,
                message_ts TEXT NOT NULL DEFAULT '',
                digest TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (codex_thread_id, turn_id)
            );
            """
        )
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _binding(row: Optional[sqlite3.Row]) -> Optional[Binding]:
        if row is None:
            return None
        return Binding(
            team_id=str(row["team_id"]),
            channel_id=str(row["channel_id"]),
            thread_ts=str(row["thread_ts"]),
            user_id=str(row["user_id"]),
            codex_thread_id=str(row["codex_thread_id"]),
            cwd=str(row["cwd"]),
            created_at=float(row["created_at"]),
            last_used_at=float(row["last_used_at"]),
        )

    def create(self, binding: Binding) -> None:
        with self._lock, self._connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO bindings(
                        team_id, channel_id, thread_ts, user_id,
                        codex_thread_id, cwd, created_at, last_used_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        binding.team_id,
                        binding.channel_id,
                        binding.thread_ts,
                        binding.user_id,
                        binding.codex_thread_id,
                        binding.cwd,
                        binding.created_at,
                        binding.last_used_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise BindingConflict(str(exc)) from exc

    def by_slack(self, team_id: str, channel_id: str, thread_ts: str) -> Optional[Binding]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM bindings
                WHERE team_id=? AND channel_id=? AND thread_ts=?
                """,
                (team_id, channel_id, thread_ts),
            ).fetchone()
        return self._binding(row)

    def by_codex(self, codex_thread_id: str) -> Optional[Binding]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM bindings WHERE codex_thread_id=?",
                (codex_thread_id,),
            ).fetchone()
        return self._binding(row)

    def by_codex_many(self, codex_thread_ids: Iterable[str]) -> dict[str, Binding]:
        ids = [str(value) for value in codex_thread_ids if value]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM bindings WHERE codex_thread_id IN ({placeholders})",
                ids,
            ).fetchall()
        result: dict[str, Binding] = {}
        for row in rows:
            binding = self._binding(row)
            if binding is not None:
                result[binding.codex_thread_id] = binding
        return result

    def touch(self, binding: Binding) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                UPDATE bindings SET last_used_at=?
                WHERE team_id=? AND channel_id=? AND thread_ts=?
                """,
                (time.time(), binding.team_id, binding.channel_id, binding.thread_ts),
            )

    def in_channel(self, channel_id: str) -> list[Binding]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM bindings WHERE channel_id=? ORDER BY last_used_at DESC",
                (channel_id,),
            ).fetchall()
        return [self._binding(row) for row in rows]

    def progress(self, thread_id: str, turn_id: str = "") -> Optional[dict]:
        with self._lock, self._connection() as conn:
            if turn_id:
                row = conn.execute(
                    "SELECT * FROM progress WHERE codex_thread_id=? AND turn_id=?",
                    (thread_id, turn_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM progress WHERE codex_thread_id=? ORDER BY rowid DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()
        return dict(row) if row else None

    def save_progress(self, thread_id: str, turn_id: str, *, message_ts: str = "",
                      digest: str = "", completed: bool = False) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """INSERT INTO progress(codex_thread_id, turn_id, message_ts, digest, completed)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT(codex_thread_id, turn_id) DO UPDATE SET
                   message_ts=excluded.message_ts, digest=excluded.digest, completed=excluded.completed""",
                (thread_id, turn_id, message_ts, digest, int(completed)),
            )

    def delete(self, binding: Binding) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                DELETE FROM bindings
                WHERE team_id=? AND channel_id=? AND thread_ts=?
                """,
                (binding.team_id, binding.channel_id, binding.thread_ts),
            )
