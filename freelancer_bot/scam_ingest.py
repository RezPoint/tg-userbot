"""Парсер скам-каналов → запись в skibidi.scam_blacklist / scam_credentials.

Подписывается на список каналов из env SKIBIDI_SCAM_INGEST_CHANNELS (через запятую),
для каждого нового сообщения извлекает Telegram-username скамеров, телефоны,
номера карт, UID бирж. Резолвит username → tg_id через тот же user-Telethon,
upsert в skibidi-схему.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable

import psycopg
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UsernameInvalidError, UsernameNotOccupiedError

LOG = logging.getLogger(__name__)

CARD_RE = re.compile(r"(?<!\d)(?:\d[\s-]?){13,18}\d(?!\d)")
PHONE_RE = re.compile(r"(?:\+?7|8)[\s\-(]*9\d{2}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
BYBIT_UID_RE = re.compile(r"bybit\.com/[^/]+/p2p/profile/(s[0-9a-f]+)", re.IGNORECASE)
MEXC_UID_RE = re.compile(r"mexc\.com/[^/]+/p2p/profile/(\w+)", re.IGNORECASE)
# «⏺️ Реквизит: 9203041024» или «Реквизит: 9203041024» — 10–12 цифр без BIN.
ACCOUNT_SHORT_RE = re.compile(r"[Рр]еквизит[ы]?\s*:?\s*(\d{10,12})(?!\d)")
# «🆔 UID: 502905147» — числовой Bybit/MEXC UID
BYBIT_UID_NUM_RE = re.compile(r"🆔\s*\**\s*UID\s*\**:?\s*(\d{6,12})(?!\d)")
# «🪪Никнейм: Matvey_BB» — отображаемое имя на Bybit
BYBIT_NICK_RE = re.compile(r"🪪\s*\**\s*[Нн]ик(?:нейм)?\s*\**:?\s*([A-Za-z][A-Za-z0-9_.-]{2,32})")
# auto-сгенерированный Bybit-username вида «User1234ABCD»
BYBIT_AUTO_USER_RE = re.compile(r"\bUser[0-9A-Za-z]{6,12}\b")
USERNAME_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
REPORTER_LINE_RE = re.compile(
    r"(?:🥷\s*)?(?:[Оо]тправитель|[Пп]рислал|[Ии]сточник|[Аа]втор)\s*:?\s*@[A-Za-z0-9_]+",
)
REPORTER_USERNAME_RE = re.compile(
    r"(?:🥷\s*)?(?:[Оо]тправитель|[Пп]рислал|[Ии]сточник|[Аа]втор)\s*:?\s*@([A-Za-z0-9_]+)",
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]+\)")
DIGITS_RE = re.compile(r"\d+(?:[\s\-.]*\d+)*")


def _luhn_ok(d: str) -> bool:
    s = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i & 1:
            n *= 2
            if n > 9:
                n -= 9
        s += n
    return s % 10 == 0


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11:
        return None
    if digits[0] == "8":
        digits = "7" + digits[1:]
    if digits[0] != "7":
        return None
    return "+" + digits


def _normalize_card(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if not (13 <= len(digits) <= 19):
        return None
    if not _luhn_ok(digits):
        return None
    return digits


@dataclass(frozen=True)
class Extracted:
    usernames: list[str]
    phones: list[str]
    cards: list[str]
    bybit_uids: list[str]
    mexc_uids: list[str]
    account_shorts: list[str]
    bybit_nicknames: list[str]
    bybit_uids_numeric: list[str]


def extract(text: str) -> Extracted:
    raw = text or ""
    reporters = {m.group(1).lower() for m in REPORTER_USERNAME_RE.finditer(raw)}
    clean = REPORTER_LINE_RE.sub("", raw)
    usernames = []
    seen_u: set[str] = set()
    for m in USERNAME_RE.finditer(clean):
        u = m.group(1)
        low = u.lower()
        if low.endswith("_bot") or low in seen_u or low in reporters:
            continue
        seen_u.add(low)
        usernames.append(u)

    phones, seen_p = [], set()
    for m in PHONE_RE.finditer(clean):
        v = _normalize_phone(m.group(0))
        if v and v not in seen_p:
            seen_p.add(v); phones.append(v)

    cards, seen_c = [], set()
    for m in CARD_RE.finditer(clean):
        v = _normalize_card(m.group(0))
        if v and v not in seen_c:
            seen_c.add(v); cards.append(v)

    bybit, seen_b = [], set()
    for m in BYBIT_UID_RE.finditer(clean):
        v = m.group(1)
        if v not in seen_b:
            seen_b.add(v); bybit.append(v)

    mexc, seen_m = [], set()
    for m in MEXC_UID_RE.finditer(clean):
        v = m.group(1)
        if v not in seen_m:
            seen_m.add(v); mexc.append(v)

    accs, seen_a = [], set()
    for m in ACCOUNT_SHORT_RE.finditer(clean):
        v = m.group(1)
        if v not in seen_a:
            seen_a.add(v); accs.append(v)

    nicks, seen_n = [], set()
    for m in BYBIT_NICK_RE.finditer(raw):
        v = m.group(1).strip()
        if v in {"—", "-", "?"} or v.lower() in seen_n:
            continue
        seen_n.add(v.lower())
        nicks.append(v)
    # auto-юзернеймы Bybit (UserXXXX) тоже считаем bybit_nickname
    for m in BYBIT_AUTO_USER_RE.finditer(raw):
        v = m.group(0)
        if v.lower() not in seen_n:
            seen_n.add(v.lower()); nicks.append(v)

    uids_num, seen_un = [], set()
    for m in BYBIT_UID_NUM_RE.finditer(raw):
        v = m.group(1)
        if v not in seen_un:
            seen_un.add(v); uids_num.append(v)

    return Extracted(
        usernames=usernames, phones=phones, cards=cards,
        bybit_uids=bybit, mexc_uids=mexc, account_shorts=accs,
        bybit_nicknames=nicks, bybit_uids_numeric=uids_num,
    )


async def _resolve(client: TelegramClient, username: str) -> int | None:
    try:
        ent = await client.get_entity(username)
        return getattr(ent, "id", None)
    except (UsernameInvalidError, UsernameNotOccupiedError, ValueError):
        return None
    except FloodWaitError as e:
        LOG.warning("flood wait %ss on resolve @%s", e.seconds, username)
        await asyncio.sleep(min(e.seconds, 60))
        return None
    except Exception as e:  # noqa: BLE001
        LOG.warning("resolve @%s failed: %s", username, e)
        return None


def _post_url(channel_username: str, msg_id: int) -> str:
    return f"https://t.me/{channel_username}/{msg_id}"


def _summary_from_post(text: str, also_strip: list[str] | None = None) -> str:
    cleaned = REPORTER_LINE_RE.sub("", text or "")
    for token in (also_strip or []):
        if token and len(token) >= 3:
            cleaned = re.sub(rf"\b{re.escape(token)}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = MARKDOWN_LINK_RE.sub("", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"@[A-Za-z0-9_]+", "", cleaned)
    cleaned = re.sub(r"[🆔🪪⏺️⭐🥷*️⃣][^\n]*", "", cleaned)
    # Шаблонные лейблы P2P_BlackList без эмодзи
    cleaned = re.sub(
        r"(?:Telegram\s+(?:ID|Username)|Биржа|UID|Никнейм|Никн?:|Ник)\s*:?[^\n]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Auto-Bybit юзернеймы (User0323G8HKHt) — это мусор, реальные ники в bybit_nickname
    cleaned = BYBIT_AUTO_USER_RE.sub("", cleaned)
    # Вычищаем длинные цифровые последовательности (телефоны, карты, UID — они уже в credentials)
    cleaned = re.sub(r"(?:\+?\d[\s\-()]*){10,}", "", cleaned)
    # Лишние символы форматирования
    cleaned = re.sub(r"\*+|#+|—", "", cleaned)
    # Балансировка скобок: убираем непарные ( и )
    while cleaned.count(")") > cleaned.count("("):
        cleaned = cleaned.replace(")", "", 1)
    while cleaned.count("(") > cleaned.count(")"):
        cleaned = cleaned.replace("(", "", 1)
    # Убираем пустые скобки, появившиеся после чистки
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    # Двойные/тройные пунктуации
    cleaned = re.sub(r"[,;:.]{2,}", ",", cleaned)
    cleaned = re.sub(r"[\s⠀]+", " ", cleaned).strip(" ,.;:-")
    return cleaned[:400] or "Скам в P2P (Bybit/MEXC)"


async def _store(
    dsn: str,
    extracted: Extracted,
    source_chat_id: int,
    source_msg_id: int,
    source_url: str,
    raw_text: str,
    resolved: dict[str, int | None],
    category: str = "general",
) -> int:
    summary = _summary_from_post(raw_text, also_strip=extracted.bybit_nicknames)
    written = 0
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute("SET search_path TO skibidi, public")
        async with conn.cursor() as cur:
            for uname in extracted.usernames:
                tg_id = resolved.get(uname.lower())
                if tg_id is None:
                    continue
                await cur.execute(
                    """
                    INSERT INTO scam_blacklist (target_tg_id, target_username, reason, source_chat_id, source_msg_id, category)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (target_tg_id) DO UPDATE
                    SET target_username = EXCLUDED.target_username,
                        source_chat_id  = COALESCE(scam_blacklist.source_chat_id, EXCLUDED.source_chat_id),
                        source_msg_id   = COALESCE(scam_blacklist.source_msg_id,  EXCLUDED.source_msg_id),
                        category        = CASE WHEN scam_blacklist.category = 'general' THEN EXCLUDED.category ELSE scam_blacklist.category END,
                        votes = scam_blacklist.votes + 1
                    """,
                    (tg_id, uname, summary, source_chat_id, source_msg_id, category),
                )
                written += 1

            primary_tg_id = next(
                (resolved[u.lower()] for u in extracted.usernames if resolved.get(u.lower())),
                None,
            )
            for kind, values in (
                ("phone", extracted.phones),
                ("card", extracted.cards),
                ("bybit_uid", extracted.bybit_uids),
                ("mexc_uid", extracted.mexc_uids),
                ("account_short", extracted.account_shorts),
                ("bybit_nickname", extracted.bybit_nicknames),
                ("bybit_uid_numeric", extracted.bybit_uids_numeric),
            ):
                for v in values:
                    await cur.execute(
                        """
                        INSERT INTO scam_credentials
                            (kind, value, scammer_tg_id, source_chat_id, source_msg_id, source_url, raw_text)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (kind, value, scammer_tg_id) DO NOTHING
                        """,
                        (kind, v, primary_tg_id, source_chat_id, source_msg_id, source_url, raw_text[:2000]),
                    )
                    written += 1
        await conn.commit()
    return written


async def _process_message(client: TelegramClient, dsn: str, msg, category: str = "general") -> None:
    text = msg.text or msg.message or ""
    if not text:
        return
    ext = extract(text)
    if not (
        ext.usernames or ext.phones or ext.cards or ext.bybit_uids or ext.mexc_uids
        or ext.account_shorts or ext.bybit_nicknames or ext.bybit_uids_numeric
    ):
        return

    resolved: dict[str, int | None] = {}
    for u in ext.usernames:
        resolved[u.lower()] = await _resolve(client, u)
        await asyncio.sleep(0.5)

    channel = await msg.get_chat()
    handle = getattr(channel, "username", None) or str(channel.id)
    n = await _store(
        dsn=dsn,
        extracted=ext,
        source_chat_id=channel.id,
        source_msg_id=msg.id,
        source_url=_post_url(handle, msg.id),
        raw_text=text,
        resolved=resolved,
        category=category,
    )
    LOG.info(
        "scam_ingest: чан=%s msg=%s usernames=%d phones=%d cards=%d uid=%d → %d записей",
        handle, msg.id, len(ext.usernames), len(ext.phones), len(ext.cards),
        len(ext.bybit_uids) + len(ext.mexc_uids), n,
    )


def channels_from_env() -> list[tuple[str, str]]:
    """Парсит SKIBIDI_SCAM_INGEST_CHANNELS в список (channel, category).
    Формат: `name:category` (через запятую). Если категория не указана — 'general'.
    Пример: `P2P_BlackList:p2p,SomeOtherChan` → [('P2P_BlackList','p2p'), ('SomeOtherChan','general')]
    """
    raw = os.environ.get("SKIBIDI_SCAM_INGEST_CHANNELS", "").strip()
    out: list[tuple[str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            ch, cat = item.split(":", 1)
            out.append((ch.strip().lstrip("@"), cat.strip() or "general"))
        else:
            out.append((item.lstrip("@"), "general"))
    return out


async def register_listeners(
    client: TelegramClient,
    channels: Iterable[tuple[str, str]],
    dsn: str,
    *,
    backfill_on_start: int = 0,
) -> None:
    chan_list = list(channels)
    for ch, cat in chan_list:
        try:
            entity = await client.get_entity(ch)
        except Exception as e:  # noqa: BLE001
            LOG.warning("scam_ingest: не смог открыть @%s: %s", ch, e)
            continue

        async def _handler(event, _dsn=dsn, _cat=cat):
            try:
                await _process_message(client, _dsn, event.message, category=_cat)
            except Exception:  # noqa: BLE001
                LOG.exception("scam_ingest: ошибка обработки сообщения")

        client.add_event_handler(_handler, events.NewMessage(chats=entity))
        LOG.info("scam_ingest: подписан на @%s (id=%s, category=%s)", ch, entity.id, cat)

    if backfill_on_start > 0 and chan_list:
        async def _run_backfill():
            try:
                await backfill(client, chan_list, dsn, limit=backfill_on_start)
            except Exception:  # noqa: BLE001
                LOG.exception("scam_ingest: автобэкфилл провалился")
        asyncio.create_task(_run_backfill())
        LOG.info("scam_ingest: автобэкфилл запущен в фоне (limit=%d)", backfill_on_start)


async def backfill(client: TelegramClient, channels: Iterable[tuple[str, str]], dsn: str, limit: int = 200) -> None:
    """Однократный backfill последних `limit` постов из каждого канала с категорией."""
    for ch, cat in channels:
        try:
            entity = await client.get_entity(ch)
        except Exception as e:  # noqa: BLE001
            LOG.warning("scam_ingest backfill: @%s — %s", ch, e)
            continue
        LOG.info("scam_ingest backfill: @%s, последние %d постов (category=%s)", ch, limit, cat)
        n = 0
        async for msg in client.iter_messages(entity, limit=limit):
            try:
                await _process_message(client, dsn, msg, category=cat)
                n += 1
                if n % 50 == 0:
                    LOG.info("scam_ingest backfill: @%s — прогресс %d/%d", ch, n, limit)
            except FloodWaitError as e:
                LOG.warning("scam_ingest backfill: FloodWait %ss, жду", e.seconds)
                await asyncio.sleep(min(e.seconds, 300))
            except Exception:  # noqa: BLE001
                LOG.exception("backfill error")
        LOG.info("scam_ingest backfill: @%s — обработано %d сообщений", ch, n)
