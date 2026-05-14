from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from psycopg_pool import ConnectionPool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    def __init__(self, dsn: str, _legacy_path: Path | None = None):
        if not dsn:
            raise RuntimeError("SUPABASE_DSN не задан в .env")
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=5,
            kwargs={"options": "-c search_path=freelance_radar,extensions,public"},
            open=True,
            timeout=30,
        )

    def close(self) -> None:
        self._pool.close()

    def add_subscriber(self, chat_id: int) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO subscribers(chat_id, created_at)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO NOTHING
                """,
                (chat_id, utc_now()),
            )

    def remove_subscriber(self, chat_id: int) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM subscribers WHERE chat_id = %s", (chat_id,))

    def subscribers(self) -> list[int]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM subscribers ORDER BY created_at")
            return [int(row[0]) for row in cur.fetchall()]

    def record_or_should_retry(self, lead: LeadRecord) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT notified_at FROM leads WHERE source = %s AND message_id = %s",
                (lead.source, lead.message_id),
            )
            row = cur.fetchone()
            if row is not None:
                return row[0] is None

            try:
                msg_date = datetime.fromisoformat(lead.message_date) if lead.message_date else None
            except ValueError:
                msg_date = None

            cur.execute(
                """
                INSERT INTO leads(
                    source, message_id, link, text, score, keywords,
                    message_date, notified_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s)
                ON CONFLICT (source, message_id) DO NOTHING
                """,
                (
                    lead.source,
                    lead.message_id,
                    lead.link,
                    lead.text,
                    lead.score,
                    list(lead.keywords),
                    msg_date,
                    utc_now(),
                ),
            )
            return True

    def mark_notified(self, source: str, message_id: int) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE leads SET notified_at = %s WHERE source = %s AND message_id = %s",
                (utc_now(), source, message_id),
            )

    def stats(self) -> dict[str, int]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM leads")
            leads = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM leads WHERE notified_at IS NULL")
            pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM subscribers")
            subs = cur.fetchone()[0]
        return {"leads": int(leads), "pending": int(pending), "subscribers": int(subs)}

    def add_initial_subscribers(self, chat_ids: Iterable[int]) -> None:
        for chat_id in chat_ids:
            self.add_subscriber(chat_id)

    def get_lead_id(self, source: str, message_id: int) -> int | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM leads WHERE source = %s AND message_id = %s",
                (source, message_id),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None

    def get_lead_with_classification(self, lead_id: int) -> dict | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.source, l.text, l.link, l.score,
                       c.is_match, c.priority, c.task_type, c.estimated_budget,
                       c.urgency, c.stack_match, c.summary
                FROM leads l
                LEFT JOIN classifications c ON c.lead_id = l.id
                WHERE l.id = %s
                """,
                (lead_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "source": row[0], "text": row[1], "link": row[2], "score": row[3],
                "is_match": row[4], "priority": row[5], "task_type": row[6],
                "estimated_budget": row[7], "urgency": row[8],
                "stack_match": list(row[9] or []), "summary": row[10],
            }

    def save_draft(
        self,
        lead_id: int,
        *,
        body: str,
        kb_doc_ids: list[int],
        model: str,
        tokens_used: int,
    ) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO drafts (lead_id, body, kb_doc_ids, model, tokens_used)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (lead_id) DO UPDATE SET
                    body = EXCLUDED.body,
                    kb_doc_ids = EXCLUDED.kb_doc_ids,
                    model = EXCLUDED.model,
                    tokens_used = EXCLUDED.tokens_used,
                    version = drafts.version + 1,
                    updated_at = now()
                RETURNING version
                """,
                (lead_id, body, kb_doc_ids, model, tokens_used),
            )
            return int(cur.fetchone()[0])

    def get_draft(self, lead_id: int) -> dict | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT body, kb_doc_ids, version, updated_at FROM drafts WHERE lead_id = %s",
                (lead_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "body": row[0],
                "kb_doc_ids": list(row[1] or []),
                "version": int(row[2]),
                "updated_at": row[3],
            }

    def save_classification(
        self,
        lead_id: int,
        *,
        is_match: bool,
        priority: int,
        task_type: str | None,
        estimated_budget: int | None,
        urgency: str,
        stack_match: tuple[str, ...] | list[str],
        summary: str | None,
        raw: dict,
        model: str,
        tokens_used: int,
    ) -> None:
        from psycopg.types.json import Jsonb

        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO classifications (
                    lead_id, is_match, priority, task_type, estimated_budget,
                    urgency, stack_match, summary, raw, model, tokens_used
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (lead_id) DO UPDATE SET
                    is_match = EXCLUDED.is_match,
                    priority = EXCLUDED.priority,
                    task_type = EXCLUDED.task_type,
                    estimated_budget = EXCLUDED.estimated_budget,
                    urgency = EXCLUDED.urgency,
                    stack_match = EXCLUDED.stack_match,
                    summary = EXCLUDED.summary,
                    raw = EXCLUDED.raw,
                    model = EXCLUDED.model,
                    tokens_used = EXCLUDED.tokens_used
                """,
                (
                    lead_id, is_match, priority, task_type, estimated_budget,
                    urgency, list(stack_match), summary, Jsonb(raw), model, tokens_used,
                ),
            )
