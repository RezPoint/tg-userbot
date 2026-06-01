from __future__ import annotations

import logging
import re
from aiohttp import web
from telethon import TelegramClient
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import User

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

    async def profile(request: web.Request) -> web.Response:
        if not user_client.is_connected():
            return web.json_response(
                {"found": False, "reason": "telethon_disconnected"}, status=503,
            )
        raw = (request.query.get("username") or "").strip().lstrip("@")
        if not raw or not _USERNAME_RE.match(raw):
            return web.json_response(
                {"found": False, "reason": "invalid_username"}, status=400,
            )
        try:
            entity = await user_client.get_entity(raw)
            if not isinstance(entity, User):
                return web.json_response({"found": False, "reason": "not_a_user"})
            full = await user_client(GetFullUserRequest(entity))
            return web.json_response({
                "found": True,
                "username": getattr(entity, "username", None),
                "has_photo": getattr(entity, "photo", None) is not None,
                "bio": getattr(full.full_user, "about", None),
                "scam": bool(getattr(entity, "scam", False)),
                "fake": bool(getattr(entity, "fake", False)),
                "premium": bool(getattr(entity, "premium", False)),
                "verified": bool(getattr(entity, "verified", False)),
            })
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("profile %s failed: %s", raw, e)
            return web.json_response({"found": False, "reason": "not_found_or_flood"})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/resolve", resolve)
    app.router.add_get("/profile", profile)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOGGER.info("http server: http://0.0.0.0:%d (/health, /resolve, /profile)", port)
    return runner
