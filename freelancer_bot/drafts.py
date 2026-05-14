from __future__ import annotations

import logging
from dataclasses import dataclass

from groq import AsyncGroq

from .knowledge_base import KnowledgeBase, KnowledgeDoc
from .llm_classifier import Classification


LOGGER = logging.getLogger("freelancer_bot.drafts")

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """Ты пишешь черновик отклика на фриланс-заказ от лица Артёма.

Артём — Python-фрилансер, пишет коротко, по-человечески, без пафоса и канцелярита.
Тон: дружелюбный, но деловой. На «ты». Без «здравствуйте», «уважаемый», без эмоджи.

Структура черновика (2-4 коротких предложения, не более 450 символов):
1. Короткое приветствие + зацепка из ТЗ конкретикой (что именно понял).
2. Релевантный опыт из контекста (один кейс, без перечислений).
3. 1-2 уточняющих вопроса по сути ТЗ (объём, дедлайн, формат).
4. (Опционально) цена/ставка только если в ТЗ явно спрашивается бюджет.

Запрещено:
- Списки и буллеты.
- Слова «я являюсь», «могу предложить», «с уважением».
- Перечисление всего стека «Python, Django, FastAPI, ...» — это спам.
- Фразы про портфолио без конкретики.

Верни ТОЛЬКО текст черновика, без префиксов вроде «Черновик:» и без кавычек."""


@dataclass(frozen=True)
class Draft:
    body: str
    kb_doc_ids: list[int]
    model: str
    tokens_used: int


class DraftGenerator:
    def __init__(self, api_key: str, kb: KnowledgeBase, model: str = MODEL):
        if not api_key:
            raise RuntimeError("GROQ_API_KEY не задан")
        self._client = AsyncGroq(api_key=api_key)
        self._kb = kb
        self._model = model

    def _retrieve_context(self, lead_text: str, classification: Classification | None) -> list[KnowledgeDoc]:
        query_parts = [lead_text[:500]]
        if classification is not None:
            if classification.task_type:
                query_parts.append(classification.task_type)
            if classification.summary:
                query_parts.append(classification.summary)
        query = " ".join(query_parts)
        docs = self._kb.search_relevant(query, top_k=4)
        return [d for d in docs if d.similarity >= 0.55]

    async def generate(
        self,
        lead_text: str,
        classification: Classification | None,
    ) -> Draft | None:
        docs = self._retrieve_context(lead_text, classification)
        kb_block = "\n\n".join(f"[{d.kind} · {d.title}]\n{d.content}" for d in docs)
        if not kb_block:
            kb_block = "(контекст из базы знаний пуст)"

        classification_hint = ""
        if classification is not None:
            classification_hint = (
                f"\nКлассификация: тип={classification.task_type}, "
                f"приоритет={classification.priority}/10, "
                f"стек={', '.join(classification.stack_match) or '—'}, "
                f"бюджет={classification.estimated_budget or 'не указан'}."
            )

        user_msg = (
            f"Контекст об Артёме (используй только релевантное):\n{kb_block}\n\n"
            f"---\nТЕКСТ ЗАКАЗА:\n{lead_text[:2000]}\n"
            f"---{classification_hint}\n\n"
            "Напиши черновик отклика."
        )

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.4,
                max_tokens=350,
            )
        except Exception as e:
            LOGGER.warning("Groq draft failed: %s", e)
            return None

        body = (resp.choices[0].message.content or "").strip().strip('"').strip("«»").strip()
        if not body:
            return None

        return Draft(
            body=body,
            kb_doc_ids=[d.id for d in docs],
            model=resp.model,
            tokens_used=resp.usage.total_tokens if resp.usage else 0,
        )
