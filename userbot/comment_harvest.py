"""Постоянный харвест комментариев: раз в N часов проходит discussion-группы
всех каналов на которые подписан вспомогательный аккаунт (Nikita/@AliazPr),
вытаскивает (username, tg_id) комментаторов + inline 🆔-маппинги, пишет в
skibidi.username_id_cache.

Отдельная Telethon-сессия (sessions/nikita) — не основной @Anstantalone.
Клиент поднимается на время прохода и отключается, чтобы не держать две
живые user-сессии одновременно.
"""
from __future__ import annotations

import asyncio
import logging
import re

import psycopg
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import Channel

LOG = logging.getLogger("userbot.comment_harvest")

USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{3,31})")
TME_RE = re.compile(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,31})")
ID_MARKER_RE = re.compile(
    r"(?:🆔\s*@?|\[|\(\s*id[:\s]*|ID[:\s]+)(\d{6,12})\b", re.IGNORECASE,
)


def _extract_inline(text: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for m in ID_MARKER_RE.finditer(text):
        tg = int(m.group(1))
        before = text[: m.start()]
        link = at = None
        for x in TME_RE.finditer(before):
            link = x
        for x in USERNAME_RE.finditer(before):
            at = x
        u = None
        if link and at:
            u = (link if link.end() > at.end() else at).group(1)
        elif link:
            u = link.group(1)
        elif at:
            u = at.group(1)
        if u:
            out.append((u.lower(), tg))
    return out


async def _harvest_once(
    api_id: int, api_hash: str, dsn: str, session_path: str,
    per_channel_limit: int,
) -> int:
    pairs: dict[str, int] = {}
    async with TelegramClient(session_path, api_id, api_hash) as c:
        if not await c.is_user_authorized():
            LOG.warning("harvest: сессия %s не авторизована, пропускаю", session_path)
            return 0
        discussion: list[tuple[str, int]] = []
        async for d in c.iter_dialogs(limit=300):
            ent = d.entity
            if isinstance(ent, Channel) and getattr(ent, "broadcast", False):
                try:
                    full = await c(GetFullChannelRequest(ent))
                    linked = full.full_chat.linked_chat_id
                    if linked:
                        discussion.append((ent.username or str(ent.id), linked))
                except Exception as e:  # noqa: BLE001
                    LOG.debug("full %s: %s", ent.id, type(e).__name__)
                await asyncio.sleep(2)
        LOG.info("harvest: discussion-групп %d", len(discussion))

        for name, linked in discussion:
            cnt = 0
            try:
                async for msg in c.iter_messages(linked, limit=per_channel_limit):
                    cnt += 1
                    try:
                        snd = await msg.get_sender()
                        if snd and getattr(snd, "username", None) and not getattr(snd, "bot", False):
                            pairs[snd.username.lower()] = snd.id
                    except Exception:  # noqa: BLE001
                        pass
                    if msg.text:
                        for u, tg in _extract_inline(msg.text):
                            pairs[u] = tg
            except FloodWaitError as e:
                LOG.warning("harvest: flood %ss на @%s — стоп прохода", e.seconds, name)
                break
            except Exception as e:  # noqa: BLE001
                LOG.debug("harvest @%s: %s", name, type(e).__name__)
                continue
            await asyncio.sleep(5)

    if pairs:
        with psycopg.connect(dsn) as conn:
            conn.execute("SET search_path TO skibidi, public")
            with conn.cursor() as cur:
                for uname, tg in pairs.items():
                    cur.execute(
                        "INSERT INTO username_id_cache (username, tg_id) "
                        "VALUES (lower(%s), %s) "
                        "ON CONFLICT (username) DO UPDATE "
                        "SET last_seen=now(), seen_count=username_id_cache.seen_count+1, "
                        "    tg_id=EXCLUDED.tg_id",
                        (uname, tg),
                    )
            conn.commit()
    LOG.info("harvest: собрано %d уникальных маппингов", len(pairs))
    return len(pairs)


async def run_harvest_loop(
    api_id: int, api_hash: str, dsn: str, session_path: str,
    interval_s: int = 6 * 3600, startup_delay_s: int = 300,
    per_channel_limit: int = 2000,
) -> None:
    LOG.info(
        "comment_harvest loop: session=%s startup_delay=%ss interval=%ss",
        session_path, startup_delay_s, interval_s,
    )
    await asyncio.sleep(startup_delay_s)
    while True:
        try:
            await _harvest_once(api_id, api_hash, dsn, session_path, per_channel_limit)
        except Exception:  # noqa: BLE001
            LOG.exception("comment_harvest crashed (continue)")
        await asyncio.sleep(interval_s)
