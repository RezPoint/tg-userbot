from __future__ import annotations

import logging
import re
from aiohttp import web
from telethon import TelegramClient

from .scam_ingest import _resolve

LOGGER = logging.getLogger("userbot.health")

_USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


async def start_health_server(user_client: TelegramClient, port: int = 8083) -> web.AppRunner:
    async def health(_request: web.Request) -> web.Response:
        if not user_client.is_connected():
            return web.json_response({"ok": False, "reason": "telethon_disconnected"}, status=503)
        return web.json_response({"ok": True})

    async def resolve(request: web.Request) -> web.Response:
        if not user_client.is_connected():
            return web.json_response(
                {"tg_id": None, "reason": "telethon_disconnected"}, status=503,
            )
        raw = (request.query.get("username") or "").strip().lstrip("@")
        if not raw or not _USERNAME_RE.match(raw):
            return web.json_response(
                {"tg_id": None, "reason": "invalid_username"}, status=400,
            )
        try:
            tg_id = await _resolve(user_client, raw)
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("resolve %s failed: %s", raw, e)
            return web.json_response(
                {"tg_id": None, "reason": "internal_error"}, status=500,
            )
        if tg_id is None:
            return web.json_response({"tg_id": None, "reason": "not_found_or_flood"})
        return web.json_response({"tg_id": int(tg_id)})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/resolve", resolve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOGGER.info("http server: http://0.0.0.0:%d (/health, /resolve)", port)
    return runner
