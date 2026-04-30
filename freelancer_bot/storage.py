from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LeadRecord:
    source: str
    message_id: int
    link: str
    text: str
    score: int
    keywords: tuple[str, ...]
    message_date: str


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                link TEXT NOT NULL,
                text TEXT NOT NULL,
                score INTEGER NOT NULL,
                keywords_json TEXT NOT NULL,
                message_date TEXT NOT NULL,
                notified_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(source, message_id)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_subscriber(self, chat_id: int) -> None:
        self._conn.execute(
            """
            INSERT INTO subscribers(chat_id, created_at)
            VALUES(?, ?)
            ON CONFLICT(chat_id) DO NOTHING
            """,
            (chat_id, utc_now()),
        )
        self._conn.commit()

    def remove_subscriber(self, chat_id: int) -> None:
        self._conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        self._conn.commit()

    def subscribers(self) -> list[int]:
        rows = self._conn.execute("SELECT chat_id FROM subscribers ORDER BY created_at").fetchall()
        return [int(row["chat_id"]) for row in rows]

    def record_or_should_retry(self, lead: LeadRecord) -> bool:
        existing = self._conn.execute(
            "SELECT notified_at FROM leads WHERE source = ? AND message_id = ?",
            (lead.source, lead.message_id),
        ).fetchone()
        if existing:
            return existing["notified_at"] is None

        self._conn.execute(
            """
            INSERT INTO leads(
                source, message_id, link, text, score, keywords_json,
                message_date, notified_at, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                lead.source,
                lead.message_id,
                lead.link,
                lead.text,
                lead.score,
                json.dumps(list(lead.keywords), ensure_ascii=False),
                lead.message_date,
                utc_now(),
            ),
        )
        self._conn.commit()
        return True

    def mark_notified(self, source: str, message_id: int) -> None:
        self._conn.execute(
            "UPDATE leads SET notified_at = ? WHERE source = ? AND message_id = ?",
            (utc_now(), source, message_id),
        )
        self._conn.commit()

    def stats(self) -> dict[str, int]:
        lead_count = self._conn.execute("SELECT COUNT(*) AS count FROM leads").fetchone()["count"]
        pending_count = self._conn.execute(
            "SELECT COUNT(*) AS count FROM leads WHERE notified_at IS NULL"
        ).fetchone()["count"]
        subscriber_count = self._conn.execute("SELECT COUNT(*) AS count FROM subscribers").fetchone()[
            "count"
        ]
        return {
            "leads": int(lead_count),
            "pending": int(pending_count),
            "subscribers": int(subscriber_count),
        }

    def add_initial_subscribers(self, chat_ids: Iterable[int]) -> None:
        for chat_id in chat_ids:
            self.add_subscriber(chat_id)

