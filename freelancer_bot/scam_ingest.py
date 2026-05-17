"""Парсер скам-каналов → запись в skibidi.scam_blacklist / scam_credentials.

Подписывается на список каналов из env SKIBIDI_SCAM_INGEST_CHANNELS (через запятую),
для каждого нового сообщения извлекает Telegram-username скаммеров, телефоны,
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
USERNAME_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
REPORTER_LINE_RE = re.compile(r"🥷\s*[Оо]тправитель:?\s*@[A-Za-z0-9_]+")


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


def extract(text: str) -> Extracted:
    clean = REPORTER_LINE_RE.sub("", text or "")
    usernames = []
    seen_u: set[str] = set()
    for m in USERNAME_RE.finditer(clean):
        u = m.group(1)
        low = u.lower()
        if low.endswith("_bot") or low in seen_u:
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

    return Extracted(usernames=usernames, phones=phones, cards=cards, bybit_uids=bybit, mexc_uids=mexc)


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


async def _store(
    dsn: str,
    extracted: Extracted,
    source_chat_id: int,
    source_msg_id: int,
    source_url: str,
    raw_text: str,
    resolved: dict[str, int | None],
) -> int:
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
                    INSERT INTO scam_blacklist (target_tg_id, target_username, reason, public_chat_id, public_msg_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (target_tg_id) DO UPDATE
                    SET target_username = EXCLUDED.target_username,
                        votes = scam_blacklist.votes + 1
                    """,
                    (tg_id, uname, f"Из канала-источника, см. {source_url}", source_chat_id, source_msg_id),
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


async def _process_message(client: TelegramClient, dsn: str, msg) -> None:
    text = msg.text or msg.message or ""
    if not text:
        return
    ext = extract(text)
    if not (ext.usernames or ext.phones or ext.cards or ext.bybit_uids or ext.mexc_uids):
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
    )
    LOG.info(
        "scam_ingest: чан=%s msg=%s usernames=%d phones=%d cards=%d uid=%d → %d записей",
        handle, msg.id, len(ext.usernames), len(ext.phones), len(ext.cards),
        len(ext.bybit_uids) + len(ext.mexc_uids), n,
    )


def channels_from_env() -> list[str]:
    raw = os.environ.get("SKIBIDI_SCAM_INGEST_CHANNELS", "").strip()
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


async def register_listeners(
    client: TelegramClient,
    channels: Iterable[str],
    dsn: str,
    *,
    backfill_on_start: int = 0,
) -> None:
    chan_list = list(channels)
    for ch in chan_list:
        try:
            entity = await client.get_entity(ch)
        except Exception as e:  # noqa: BLE001
            LOG.warning("scam_ingest: не смог открыть @%s: %s", ch, e)
            continue

        async def _handler(event, _dsn=dsn):
            try:
                await _process_message(client, _dsn, event.message)
            except Exception:  # noqa: BLE001
                LOG.exception("scam_ingest: ошибка обработки сообщения")

        client.add_event_handler(_handler, events.NewMessage(chats=entity))
        LOG.info("scam_ingest: подписан на @%s (id=%s)", ch, entity.id)

    if backfill_on_start > 0 and chan_list:
        async def _run_backfill():
            try:
                await backfill(client, chan_list, dsn, limit=backfill_on_start)
            except Exception:  # noqa: BLE001
                LOG.exception("scam_ingest: автобэкфилл провалился")
        asyncio.create_task(_run_backfill())
        LOG.info("scam_ingest: автобэкфилл запущен в фоне (limit=%d)", backfill_on_start)


async def backfill(client: TelegramClient, channels: Iterable[str], dsn: str, limit: int = 200) -> None:
    """Однократный backfill последних `limit` постов из каждого канала."""
    for ch in channels:
        try:
            entity = await client.get_entity(ch)
        except Exception as e:  # noqa: BLE001
            LOG.warning("scam_ingest backfill: @%s — %s", ch, e)
            continue
        LOG.info("scam_ingest backfill: @%s, последние %d постов", ch, limit)
        n = 0
        async for msg in client.iter_messages(entity, limit=limit):
            try:
                await _process_message(client, dsn, msg)
                n += 1
            except Exception:  # noqa: BLE001
                LOG.exception("backfill error")
        LOG.info("scam_ingest backfill: @%s — обработано %d сообщений", ch, n)
