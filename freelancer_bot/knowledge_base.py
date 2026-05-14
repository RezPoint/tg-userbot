from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import Iterable

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


LOGGER = logging.getLogger("freelancer_bot.kb")

EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
EMBEDDING_DIM = 768


@dataclass(frozen=True)
class KnowledgeDoc:
    id: int
    kind: str
    title: str
    content: str
    metadata: dict
    similarity: float


class KnowledgeBase:
    """RAG-обёртка над freelance_radar.knowledge_base + pgvector.

    Embeddings локально через sentence-transformers — без затрат на API.
    Модель грузится один раз lazy при первом обращении.
    """

    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    @cached_property
    def _model(self):
        from sentence_transformers import SentenceTransformer
        LOGGER.info("Loading embedding model %s ...", EMBEDDING_MODEL)
        return SentenceTransformer(EMBEDDING_MODEL)

    def _embed(self, text: str, *, is_query: bool) -> list[float]:
        prefix = "query: " if is_query else "passage: "
        vec = self._model.encode([prefix + text], normalize_embeddings=True)[0]
        return vec.tolist()

    def add_document(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        embedding = self._embed(content, is_query=False)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge_base (kind, title, content, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (kind, title, content, str(embedding), Jsonb(metadata or {})),
            )
            return int(cur.fetchone()[0])

    def upsert_document(
        self,
        *,
        title: str,
        kind: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        """Перезаписать документ по title (используется при reseed профиля)."""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM knowledge_base WHERE title = %s", (title,))
            row = cur.fetchone()
            if row:
                doc_id = int(row[0])
                embedding = self._embed(content, is_query=False)
                cur.execute(
                    """
                    UPDATE knowledge_base SET
                        kind = %s, content = %s, embedding = %s,
                        metadata = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (kind, content, str(embedding), Jsonb(metadata or {}), doc_id),
                )
                return doc_id
        return self.add_document(kind=kind, title=title, content=content, metadata=metadata)

    def search_relevant(self, query: str, top_k: int = 5) -> list[KnowledgeDoc]:
        embedding = self._embed(query, is_query=True)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, kind, title, content, metadata,
                       1 - (embedding <=> %s::extensions.vector) AS similarity
                FROM knowledge_base
                WHERE enabled = TRUE AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::extensions.vector
                LIMIT %s
                """,
                (str(embedding), str(embedding), top_k),
            )
            rows = cur.fetchall()
        return [
            KnowledgeDoc(
                id=int(r[0]), kind=r[1], title=r[2], content=r[3],
                metadata=r[4] or {}, similarity=float(r[5]),
            )
            for r in rows
        ]

    def stats(self) -> dict:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE enabled = TRUE) AS enabled,
                    COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS with_embedding
                FROM knowledge_base
                """
            )
            row = cur.fetchone()
        return {"total": int(row[0]), "enabled": int(row[1]), "with_embedding": int(row[2])}


# ── базовый набор документов для первичного наполнения ────────────────────

INITIAL_DOCUMENTS: list[dict] = [
    {
        "kind": "profile",
        "title": "Профиль: Артём",
        "content": (
            "Артём — Python-разработчик-фрилансер, опыт 2+ года. "
            "Стек: Python (asyncio, asyncpg, Quart, FastAPI), PostgreSQL, "
            "Telegram-боты (Telethon, Aiogram), парсинг API Wildberries и Ozon, "
            "веб-сервисы для аналитики маркетплейсов, деплой на Amvera. "
            "Хорошо умеет: автоматизация рутинных задач, агрегаторы данных, "
            "сбор статистики, инкрементальная синхронизация, обработка больших объёмов JSON, "
            "генерация Excel-отчётов через openpyxl. "
            "Не берёт: SMM, дизайн, копирайт, монтаж видео, маркетинг, фронтенд без Python-бэка, "
            "юр.услуги, бухгалтерию."
        ),
    },
    {
        "kind": "case",
        "title": "Кейс: wb-stats — сбор статистики Wildberries",
        "content": (
            "Сделал систему сбора рекламной статистики и заказов с Wildberries API "
            "для 9 кабинетов одновременно. Стек: Python asyncio, asyncpg, PostgreSQL. "
            "Воркеры собирают данные по cardstock, постингам, sales-funnel, "
            "категориям и кампаниям. Инкрементальная синхронизация через last_change_date. "
            "Защита от rate-limit, throttle через persistent state. "
            "Веб-интерфейс на Quart для отчётов и аналитики."
        ),
        "metadata": {"price_range": "от 30 000 ₽", "duration": "1-2 месяца"},
    },
    {
        "kind": "case",
        "title": "Кейс: ozon-web — аналитика Ozon Seller API",
        "content": (
            "Парсер Ozon Seller API: продукты, кампании, performance, регионы, постинги. "
            "Поддержка двух источников данных (Seller API + Performance API), "
            "сравнение метрик из разных источников. Excel-отчёты с drag-drop колонок и "
            "sticky-итогами. Backend Python+Quart, frontend vanilla JS."
        ),
        "metadata": {"price_range": "от 30 000 ₽"},
    },
    {
        "kind": "case",
        "title": "Кейс: Telegram-бот фриланс-радар",
        "content": (
            "Userbot на Telethon мониторит 13 фриланс-каналов в Telegram, "
            "фильтрует через keyword-score, классифицирует через Groq Llama 3.3 70B, "
            "сохраняет в Supabase Postgres + pgvector для RAG. "
            "Архитектура: 2 клиента (user-session + bot), асинхронный listener, "
            "psycopg connection pool, локальные embeddings через sentence-transformers."
        ),
        "metadata": {"price_range": "5-15 000 ₽"},
    },
    {
        "kind": "rate",
        "title": "Ставки: типичные цены",
        "content": (
            "Часовая ставка: 1500 ₽/час. "
            "Простой Telegram-бот (приём заявок, уведомления): 5-15 000 ₽. "
            "Парсер публичного API без авторизации: 8-20 000 ₽. "
            "Парсер с авторизацией, сложными лимитами, retry-логикой: 20-50 000 ₽. "
            "Telegram Mini App с бэкендом: от 50 000 ₽. "
            "Полноценный backend-сервис с БД и API: от 50 000 ₽. "
            "Интеграция систем (вебхуки, API, синхронизация): 15-40 000 ₽. "
            "Доработка чужого Python-проекта: 1500 ₽/час по факту."
        ),
    },
    {
        "kind": "rate",
        "title": "Минимум: меньше не беру",
        "content": (
            "Минимальный заказ — 3000 ₽ или 2 часа работы. "
            "Заказы дешевле 3000 ₽ невыгодны: на согласование и оформление уходит "
            "столько же времени, сколько на саму работу. Исключения: разовая "
            "консультация (1000-2000 ₽ за час), мелкая доработка существующего проекта."
        ),
    },
    {
        "kind": "tech_note",
        "title": "Технические оценки времени",
        "content": (
            "Простой echo/уведомления Telegram-бот: 2-4 часа. "
            "Парсер сайта с пагинацией без авторизации: 4-8 часов. "
            "Парсер API с rate-limit и инкрементальной синхронизацией: 8-20 часов. "
            "Интеграция с платёжной системой (Юкасса, СБП): 8-16 часов. "
            "CRUD-сервис с API (FastAPI+Postgres) на 5-10 endpoint'ов: 16-40 часов. "
            "Excel-отчёт со сложным форматированием (openpyxl): 4-12 часов в зависимости от "
            "количества колонок и расчётов."
        ),
    },
    {
        "kind": "template",
        "title": "Шаблон первого отклика",
        "content": (
            "Привет! Видел заказ про [конкретика из ТЗ]. "
            "Есть опыт с [релевантный кейс или технология из стека]. "
            "Могу взять. Уточни: [1-2 ключевых вопроса о ТЗ — объём данных, дедлайн, "
            "формат вывода]. Сколько ориентируешься по бюджету?"
        ),
    },
]


def seed(pool: ConnectionPool) -> dict:
    """Заполняет KB начальным набором документов. Идемпотентно (upsert по title)."""
    kb = KnowledgeBase(pool)
    inserted = 0
    for doc in INITIAL_DOCUMENTS:
        kb.upsert_document(**doc)
        inserted += 1
    return {"upserted": inserted, **kb.stats()}
