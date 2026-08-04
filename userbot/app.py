from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from telethon import TelegramClient, events

from . import scam_ingest
from .config import RuntimeConfig
from .health import start_health_server

LOGGER = logging.getLogger("userbot")


class UserBot:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.user_client = TelegramClient(
            str(config.user_session_path), config.api_id, config.api_hash,
        )
        self.bot_client = TelegramClient(
            str(config.bot_session_path), config.api_id, config.api_hash,
        )
        self._stop = asyncio.Event()
        self._backfill_lock = asyncio.Lock()

    async def run(self) -> None:
        self._register_bot_commands()
        await self.user_client.start()
        await self.bot_client.start(bot_token=self.config.bot_token)

        self._health_runner = await start_health_server(self.user_client, port=8083)

        ingest_channels = scam_ingest.channels_from_env()
        if ingest_channels and self.config.supabase_dsn:
            import os as _os
            configured_backfill = int(_os.environ.get("SKIBIDI_SCAM_INGEST_BACKFILL", "30"))
            backfill_n = min(max(configured_backfill, 0), self.config.scam_backfill_max)
            if backfill_n != configured_backfill:
                LOGGER.warning(
                    "scam_ingest: startup backfill ограничен %d вместо %d",
                    backfill_n, configured_backfill,
                )
            await scam_ingest.register_listeners(
                self.user_client, ingest_channels, self.config.supabase_dsn,
                backfill_on_start=backfill_n,
            )
            LOGGER.info(
                "scam_ingest: live на %d канал(ов) + автобэкфилл %d постов",
                len(ingest_channels), backfill_n,
            )
            # Фоновый дорезолв NULL-юзернеймов из scam_pending_usernames
            asyncio.create_task(scam_ingest.run_dorezolv_loop(
                self.user_client, self.config.supabase_dsn, interval_s=3600,
            ))
        else:
            LOGGER.warning("scam_ingest выключен: канал/SUPABASE_DSN не заданы")

        # comment_harvest перенесён в tg-tdlib (bulk_history) — @AliazPr теперь
        # обслуживается только через TDLib, без второй Telethon-сессии.

        await self._wait_until_stopped()

    async def shutdown(self) -> None:
        runner = getattr(self, "_health_runner", None)
        if runner is not None:
            await runner.cleanup()
        await self.user_client.disconnect()
        await self.bot_client.disconnect()

    def _register_bot_commands(self) -> None:
        @self.bot_client.on(events.NewMessage(pattern=r"^/start"))
        async def start(event: events.NewMessage.Event) -> None:
            await event.respond(
                "👋 <b>tg-userbot</b> запущен.\n\n"
                "Что я делаю:\n"
                "• слушаю каналы из <code>SKIBIDI_SCAM_INGEST_CHANNELS</code>\n"
                "• разбираю скам-карточки → пишу в <code>skibidi.scam_credentials</code>\n\n"
                "Команды: /status /scam_backfill [N]",
                parse_mode="html",
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^/status"))
        async def status(event: events.NewMessage.Event) -> None:
            chans = scam_ingest.channels_from_env()
            await event.respond(
                f"📡 Каналы: {len(chans)}\n"
                f"DSN: {'✅' if self.config.supabase_dsn else '❌'}\n"
                f"User session: ✅\nBot session: ✅",
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^/scam_backfill(?:\s+(\d+))?"))
        async def scam_backfill(event: events.NewMessage.Event) -> None:
            if event.sender_id not in self.config.admin_user_ids:
                await event.respond("⛔ Команда доступна только владельцу.")
                return
            chans = scam_ingest.channels_from_env()
            if not chans or not self.config.supabase_dsn:
                await event.respond("❌ SKIBIDI_SCAM_INGEST_CHANNELS или SUPABASE_DSN не заданы.")
                return
            limit = int(event.pattern_match.group(1) or 30)
            if limit < 1 or limit > self.config.scam_backfill_max:
                await event.respond(
                    f"❌ Допустимый размер backfill: 1–{self.config.scam_backfill_max}.",
                )
                return
            if self._backfill_lock.locked():
                await event.respond("⏳ Backfill уже выполняется.")
                return
            async with self._backfill_lock:
                await event.respond(f"⏳ Backfill: {len(chans)} канал(ов), по {limit} постов.")
                try:
                    await scam_ingest.backfill(
                        self.user_client, chans, self.config.supabase_dsn, limit=limit,
                    )
                    await event.respond("✅ Backfill завершён, см. логи.")
                except Exception as e:
                    LOGGER.exception("manual scam backfill failed")
                    await event.respond(f"❌ Ошибка backfill: {type(e).__name__}")

        @self.bot_client.on(events.NewMessage(pattern=r"^/stop"))
        async def stop(event: events.NewMessage.Event) -> None:
            await event.respond("ℹ️ Остановка процесса контролируется systemd/docker.")

    async def _wait_until_stopped(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                pass
        await self._stop.wait()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="tg-userbot")
    p.add_argument("--log-level", default=None)
    return p.parse_args()


async def run_app() -> None:
    args = _parse_args()
    cfg = RuntimeConfig.from_env()
    logging.basicConfig(
        level=(args.log_level or cfg.log_level).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = UserBot(cfg)
    try:
        await bot.run()
    finally:
        await bot.shutdown()


def cli() -> None:
    asyncio.run(run_app())


if __name__ == "__main__":
    cli()
