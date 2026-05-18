from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass

import aiohttp

LOG = logging.getLogger("userbot.llm_summary")

_PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b", "CEREBRAS_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
}
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq")
LLM_URL, LLM_DEFAULT_MODEL, LLM_KEY_ENV = _PROVIDERS.get(LLM_PROVIDER, _PROVIDERS["groq"])
LLM_MODEL = os.environ.get("LLM_MODEL") or os.environ.get("GROQ_MODEL") or LLM_DEFAULT_MODEL
GROQ_URL = LLM_URL  # для обратной совместимости (тесты)
GROQ_MODEL = LLM_MODEL
SYSTEM_PROMPT = """Ты — парсер скам-постов из канала P2P_BlackList в Telegram. Это сообщения от P2P-трейдеров криптобирж (Bybit, MEXC, HTX) о мошенниках, кинувших их при сделках USDT↔RUB.

Из текста ты извлекаешь данные для карточки в нашем чёрном списке. Карточка ЖЁСТКО структурирована:
- 👤 ФИО (отдельный блок)
- 📞 телефон (отдельный блок)
- 💳 карта (отдельный блок)
- 🏦 банк (отдельный блок)
- 🏷 никнейм Bybit/MEXC (отдельный блок)
- 🪙 UID биржи (отдельный блок)
- Причина: ОПИСАНИЕ ПОВЕДЕНИЯ скаммера (что он сделал)

КРИТИЧНО: «reason» — это ТОЛЬКО про *поведение* скаммера (схема обмана, что он делал). Никаких ФИО, банков, телефонов, никнеймов, UID, хэштегов, ссылок, матов, разделителей — они идут в свои блоки, не в reason.

Хорошие примеры reason:
- «Не отпустил монеты после оплаты, требовал доплату, исчез»
- «Кинул апелляцию через 3-е лицо»
- «Прислал фейк-чек и пропал»
- «Отказался выдать USDT после получения рублей»
- «Обвинил в мошенничестве, заморозил сделку»

Плохие примеры reason (так НЕ делать):
- «Иван Иванов кинул через Сбер» (имя+банк не в reason)
- «Скаммер на Сбербанк» (банк не в reason)
- «+79991234567 кинул» (телефон не в reason)
- «#дроп кинул» (хэштег не в reason)

Верни СТРОГО JSON:
{"reason": "1-2 предложения про поведение, без ФИО/банков/телефонов/карт/никнеймов/UID/хэштегов/матов",
 "full_names": ["реальные ФИО (Имя Отчество Фамилия в правильном падеже именительном)"],
 "banks": ["канонические русские названия банков: Сбербанк, Тинькофф, ВТБ, Альфа-Банк, Газпромбанк, Россельхозбанк, Райффайзен, ПСБ, МКБ, Совкомбанк, OZON Банк, WB Банк, Яндекс Банк, Точка, Открытие, Авангард, и т.д."],
 "reasons_tags": ["короткие теги поведения: 3-е лицо, апелляция, фейк-чек, дроп, не отпустил, доплата, отмена"]}

Если поле пустое — пустой массив []. Если в посте НЕТ описания скам-поведения (только реквизиты, номер карты, ФИО без объяснения) → reason = '' (пустая строка). НЕ генерируй типа «Пост с реквизитом, вероятно скам» — это бессмыслица, лучше пусто.
НЕ выдумывай данные. НЕ добавляй текст вне JSON.
Слово «скаммер» пиши с ОДНОЙ «м» — «скамер»."""


@dataclass(frozen=True)
class LLMResult:
    reason: str
    full_names: list[str]
    banks: list[str]
    reasons_tags: list[str]


_JSON_RE = re.compile(r"\{[\s\S]*\}")


async def llm_extract(session: aiohttp.ClientSession, api_key: str, text: str, *, timeout: float = 20.0) -> LLMResult | None:
    """Извлечение через LLM (Groq/Cerebras — OpenAI-совместимые). Возвращает None при ошибке."""
    if not api_key or not text:
        return None
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:4000]},
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if LLM_PROVIDER == "openrouter":
        headers["HTTP-Referer"] = "https://t.me/SkibidiSaveBot"
        headers["X-Title"] = "tg-userbot scam-ingest"
    try:
        async with session.post(LLM_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status != 200:
                body = (await r.text())[:300]
                LOG.warning("%s %s: %s", LLM_PROVIDER, r.status, body)
                return None
            data = await r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            m = _JSON_RE.search(content)
            if not m:
                LOG.warning("no JSON in LLM response (first 200 chars): %r", content[:200])
                return None
            parsed = json.loads(m.group(0))
            reason_raw = str(parsed.get("reason") or "").strip()
            # Пост-обработка reason (не тратит запросы к LLM):
            # 1. «Скаммер» → «Скамер» (один м, как принято в P2P-комьюнити)
            reason_raw = re.sub(r"\bСкамме(р[аеуыов]?)\b", r"Скаме\1", reason_raw)
            reason_raw = re.sub(r"\bскамме(р[аеуыов]?)\b", r"скаме\1", reason_raw)
            reason_raw = re.sub(r"\bСКАММЕ(Р[АЕУЫОВ]?)\b", r"СКАМЕ\1", reason_raw)
            # 2. Маты/оскорбления
            reason_raw = re.sub(
                r"\b(?:сукин\s+сын|сук[аи]|блядь|блять|пиздец|пизда|хуй|нахуй|"
                r"ох[у]?ел\w*|ебать|ёбан\w*|ебан\w*|пидор\w*|пидорс\w*|урод\w*|"
                r"тварь|мраз\w*|долбоёб\w*|чмо|нахер|еб[ау]+)\b",
                "", reason_raw, flags=re.IGNORECASE,
            )
            # 3. Двойные пробелы и запятые после вырезаний
            reason_raw = re.sub(r"\s{2,}", " ", reason_raw)
            reason_raw = re.sub(r",\s*,+", ",", reason_raw).strip(" ,.;:-")
            return LLMResult(
                reason=reason_raw,
                full_names=[str(x).strip() for x in (parsed.get("full_names") or []) if x],
                banks=[str(x).strip() for x in (parsed.get("banks") or []) if x],
                reasons_tags=[str(x).strip() for x in (parsed.get("reasons_tags") or []) if x],
            )
    except Exception as e:  # noqa: BLE001
        LOG.warning("groq request failed: %s", e)
        return None
