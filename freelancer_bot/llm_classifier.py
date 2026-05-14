from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from groq import AsyncGroq


LOGGER = logging.getLogger("freelancer_bot.llm")

MODEL = "llama-3.3-70b-versatile"

PROFILE_HINT = (
    "Заказчик — Артём, Python-разработчик-фрилансер. "
    "Основной стек: Python (asyncio, asyncpg, Quart/FastAPI), PostgreSQL, "
    "парсинг Wildberries/Ozon API, Telegram-боты (Telethon/Aiogram). "
    "Опыт ~2 года. Не берёт: дизайн, SMM, копирайт, монтаж, маркетинг, фронт без Python-бэка."
)

SYSTEM_PROMPT = f"""Ты классифицируешь фриланс-заказы из Telegram-каналов для конкретного разработчика.

{PROFILE_HINT}

Ответь СТРОГО в JSON формате, без markdown и пояснений:
{{
  "is_match": true|false,
  "priority": 0-10,
  "task_type": "telegram-бот|парсер|API-сервис|backend|интеграция|автоматизация|вакансия|другое",
  "estimated_budget_rub": <число или null>,
  "urgency": "low|medium|high|unknown",
  "stack_match": ["список твоих навыков, реально применимых"],
  "summary": "<краткое резюме до 100 символов, что нужно сделать>"
}}

Правила:
- is_match=true только если заказ реально в стеке Артёма (Python, боты, парсинг, API).
- priority: 10=супер релевантно + хороший бюджет + срочно; 7-9=релевантно; 4-6=частично; 0-3=мусор.
- estimated_budget_rub: число в рублях если упомянуто; null если не указано.
- urgency: "high" если "срочно/asap/сегодня", "low" если "можно не спешить", "unknown" если не сказано.
- stack_match: только из стека Артёма (Python, Telegram, asyncio, asyncpg, FastAPI, Quart, парсинг WB/Ozon, PostgreSQL).
- НЕ выдумывай данные. Если не уверен — null.

Игнорируй: дизайн, SMM, копирайт, маркетинг, монтаж видео, продажи, реклама."""


@dataclass(frozen=True)
class Classification:
    is_match: bool
    priority: int
    task_type: str | None
    estimated_budget: int | None
    urgency: str
    stack_match: tuple[str, ...]
    summary: str | None
    raw: dict[str, Any]
    model: str
    tokens_used: int


def _coerce_priority(val: Any) -> int:
    try:
        p = int(val)
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, p))


def _coerce_urgency(val: Any) -> str:
    s = str(val or "").strip().lower()
    return s if s in {"low", "medium", "high", "unknown"} else "unknown"


def _coerce_budget(val: Any) -> int | None:
    if val is None:
        return None
    try:
        n = int(float(val))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


class LeadClassifier:
    def __init__(self, api_key: str, model: str = MODEL):
        if not api_key:
            raise RuntimeError("GROQ_API_KEY не задан в .env")
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def classify(self, source_title: str, text: str) -> Classification | None:
        user_msg = f"Канал: {source_title}\n\nТекст заказа:\n{text[:2000]}"
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            LOGGER.warning("Groq classify failed: %s", e)
            return None

        content = resp.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            LOGGER.warning("Groq returned non-JSON: %s", content[:200])
            return None

        return Classification(
            is_match=bool(data.get("is_match")),
            priority=_coerce_priority(data.get("priority")),
            task_type=(str(data["task_type"]).strip() if data.get("task_type") else None),
            estimated_budget=_coerce_budget(data.get("estimated_budget_rub")),
            urgency=_coerce_urgency(data.get("urgency")),
            stack_match=tuple(
                str(s).strip() for s in (data.get("stack_match") or []) if str(s).strip()
            )[:10],
            summary=(str(data["summary"]).strip()[:200] if data.get("summary") else None),
            raw=data,
            model=resp.model,
            tokens_used=resp.usage.total_tokens if resp.usage else 0,
        )
