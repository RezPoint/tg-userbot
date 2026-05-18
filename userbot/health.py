from __future__ import annotations

import logging
from aiohttp import web
from telethon import TelegramClient

LOGGER = logging.getLogger("userbot.health")


async def start_health_server(user_client: TelegramClient, port: int = 8083) -> web.AppRunner:
    async def health(_request: web.Request) -> web.Response:
        if not user_client.is_connected():
            return web.json_response({"ok": False, "reason": "telethon_disconnected"}, status=503)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    LOGGER.info("health server: http://0.0.0.0:%d/health", port)
    return runner
