from __future__ import annotations

import re
from dataclasses import dataclass


MIN_SCORE = 4

KEYWORDS: dict[str, int] = {
    "telegram bot": 5,
    "telegram-бот": 5,
    "телеграм бот": 5,
    "телеграм-бот": 5,
    "тг бот": 5,
    "tg bot": 5,
    "бот в тг": 5,
    "telethon": 5,
    "aiogram": 5,
    "pyrogram": 5,
    "bot api": 4,
    "mini app": 5,
    "mini apps": 5,
    "web app telegram": 4,
    "webapp": 3,
    "бот для": 3,
    "чат-бот": 3,
    "userbot": 5,
    "юзербот": 5,

    "парсер": 4,
    "парсинг": 4,
    "scrapy": 4,
    "beautifulsoup": 4,
    "playwright": 3,
    "selenium": 3,

    "wildberries": 5,
    "вайлдберриз": 5,
    "wb api": 5,
    "wb-api": 5,
    "ozon": 5,
    "озон": 5,
    "ozon api": 5,
    "маркетплейс api": 4,

    "python": 3,
    "питон": 3,
    "fastapi": 5,
    "django": 4,
    "flask": 3,
    "asyncio": 5,
    "asyncpg": 5,
    "quart": 4,
    "aiohttp": 4,
    "postgresql": 3,
    "postgres": 3,
    "backend python": 5,
    "бекенд python": 5,
    "python разработчик": 5,
    "python-разработчик": 5,

    "ai агент": 4,
    "ии агент": 4,
    "openai api": 4,
    "claude api": 4,
    "anthropic api": 4,
    "llm": 2,
    "gpt api": 3,

    "rest api": 3,
    "интеграция api": 4,
    "api интеграция": 4,
    "webhook": 3,
    "вебхук": 3,
    "api": 1,
    "интеграция": 1,
    "скрипт": 1,
    "автоматизация": 1,
    "нейросеть": 1,
}

STOP_WORDS: list[str] = [
    "smm",
    "смм",
    "таргетолог",
    "директолог",
    "маркетолог",
    "копирайтер",
    "рерайтер",
    "дизайнер логотип",
    "иллюстратор",
    "монтажер",
    "монтаж видео",
    "рилсмейкер",
    "рилс",
    "reels",
    "shorts",
    "тикток",
    "tiktok",
    "сторисмейкер",
    "ассистент",
    "менеджер по продажам",
    "оператор",
    "колл-центр",
    "набор текста",
    "расшифровка аудио",
    "отзывы",
    "оставлять отзывы",
    "лайки",
    "подписки",
    "без опыта",
    "ежедневные выплаты",
    "инвестиции",
    "ставки",
    "букмекер",
    "казино",
    "gambling",
    "onlyfans",
    "18+",
    "офис",
    "только офис",
    "полный день в офисе",
    "wordpress",
    "вордпресс",
    "tilda",
    "тильда",
    "битрикс",
    "bitrix",
    "1c-битрикс",
    "joomla",
    "modx",
    "opencart",
    "сайт визитка",
    "сайт-визитка",
    "лендинг",
    "landing page",
    "одностраничник",
    "верстка",
    "верстальщик",
    "qa тестировщик",
    "qa-тестировщик",
    "тестировщик",
    "manual qa",
    "автотест",
    "selenium тестер",
    "1с программист",
    "1с разработчик",
    "solidworks",
    "автокад",
    "autocad",
    "макросы",
    "vba",
    "генерация картинок",
    "сгенерировать картинки",
    "midjourney",
    "stable diffusion",
    "нейроарт",
    "ии-картинки",
    "ии картинки",
    "генератор изображений",
    "вк бот",
    "вк-бот",
    "vk бот",
    "viber бот",
    "whatsapp бот",
    "react разработчик",
    "react-разработчик",
    "frontend разработчик",
    "vue разработчик",
    "angular разработчик",
    "ios разработчик",
    "android разработчик",
    "flutter разработчик",
    "unity разработчик",
    "сео специалист",
    "seo специалист",
    "сео-оптимизация",
    "контекстная реклама",
    "яндекс директ",
]


@dataclass(frozen=True)
class MatchResult:
    accepted: bool
    score: int
    matched_keywords: tuple[str, ...]
    rejected_by: tuple[str, ...]


def normalize(text: str) -> str:
    lowered = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", lowered).strip()


def find_terms(text: str, terms: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = normalize(text)
    matches: list[str] = []
    for term in terms:
        term_normalized = normalize(term)
        if term_normalized and term_normalized in normalized:
            matches.append(term)
    return tuple(matches)


def match_text(text: str) -> MatchResult:
    rejected_by = find_terms(text, tuple(STOP_WORDS))
    if rejected_by:
        return MatchResult(False, 0, (), rejected_by)

    normalized = normalize(text)
    matched: list[str] = []
    score = 0
    for keyword, weight in KEYWORDS.items():
        if normalize(keyword) in normalized:
            matched.append(keyword)
            score += weight

    accepted = score >= MIN_SCORE
    return MatchResult(accepted, score, tuple(matched), ())
