from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Iterable

from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.custom.message import Message

from .config import RuntimeConfig
from .drafts import DraftGenerator
from .filters import KEYWORDS, STOP_WORDS, match_text
from .formatting import format_draft, format_lead
from .knowledge_base import KnowledgeBase
from .llm_classifier import Classification, LeadClassifier
from .sources import Source, enabled_sources
from .storage import LeadRecord, Storage


DRAFT_PRIORITY_THRESHOLD = 6


LOGGER = logging.getLogger("freelancer_bot")


class LeadBot:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        config.user_session_path.parent.mkdir(parents=True, exist_ok=True)
        config.bot_session_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage = Storage(config.supabase_dsn)
        if config.groq_api_key:
            self.classifier = LeadClassifier(config.groq_api_key)
            self.kb = KnowledgeBase(self.storage._pool)
            self.drafter = DraftGenerator(config.groq_api_key, self.kb)
        else:
            self.classifier = None
            self.kb = None
            self.drafter = None
        self.sources = enabled_sources()
        self.user_client = TelegramClient(
            str(config.user_session_path),
            config.api_id,
            config.api_hash,
        )
        self.bot_client = TelegramClient(
            str(config.bot_session_path),
            config.api_id,
            config.api_hash,
        )

    async def run(self) -> None:
        self._register_bot_commands()

        await self.user_client.start()
        await self.bot_client.start(bot_token=self.config.bot_token)

        if self.config.target_chat_id is not None:
            self.storage.add_subscriber(self.config.target_chat_id)

        active_sources = await self._register_source_handlers()
        LOGGER.info("Monitoring %s Telegram sources", len(active_sources))

        if self.config.send_catch_up and self.config.catch_up_limit > 0:
            await self._catch_up(active_sources)

        await self._wait_until_stopped()

    async def shutdown(self) -> None:
        await self.user_client.disconnect()
        await self.bot_client.disconnect()
        self.storage.close()

    def _register_bot_commands(self) -> None:
        @self.bot_client.on(events.NewMessage(pattern=r"^/start"))
        async def start(event: events.NewMessage.Event) -> None:
            chat_id = int(event.chat_id)
            self.storage.add_subscriber(chat_id)
            await event.respond(
                "Готово. Этот чат подписан на лиды.\n\n"
                f"Chat ID: <code>{chat_id}</code>\n"
                "Команды: /status, /sources, /keywords, /draft id, /redraft id, /test текст, /stop",
                parse_mode="html",
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^/stop"))
        async def stop(event: events.NewMessage.Event) -> None:
            self.storage.remove_subscriber(int(event.chat_id))
            await event.respond("Ок, этот чат отписан от уведомлений.")

        @self.bot_client.on(events.NewMessage(pattern=r"^/status"))
        async def status(event: events.NewMessage.Event) -> None:
            stats = self.storage.stats()
            await event.respond(
                "Статус:\n"
                f"- источников: {len(self.sources)}\n"
                f"- подписчиков: {stats['subscribers']}\n"
                f"- лидов в базе: {stats['leads']}\n"
                f"- ожидают повторной отправки: {stats['pending']}"
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^/sources"))
        async def sources(event: events.NewMessage.Event) -> None:
            lines = [f"{index}. {source.handle} — {source.title}" for index, source in enumerate(self.sources, 1)]
            await event.respond("Активные источники:\n" + "\n".join(lines))

        @self.bot_client.on(events.NewMessage(pattern=r"^/keywords"))
        async def keywords(event: events.NewMessage.Event) -> None:
            keyword_preview = ", ".join(list(KEYWORDS.keys())[:35])
            stop_preview = ", ".join(STOP_WORDS[:35])
            await event.respond(
                "Ключевые слова:\n"
                f"{keyword_preview}\n\n"
                "Стоп-слова:\n"
                f"{stop_preview}"
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^/(?:re)?draft(?:\s+(\d+))?"))
        async def draft_cmd(event: events.NewMessage.Event) -> None:
            if self.drafter is None:
                await event.respond("Черновики недоступны: GROQ_API_KEY не задан.")
                return
            raw_id = event.pattern_match.group(1)
            if not raw_id:
                await event.respond("Пришли так: <code>/draft 73</code>", parse_mode="html")
                return
            lead_id = int(raw_id)
            force = event.raw_text.startswith("/redraft")

            if not force:
                cached = self.storage.get_draft(lead_id)
                if cached is not None:
                    await event.respond(
                        format_draft(lead_id, cached["body"], version=cached["version"]),
                        parse_mode="html",
                        link_preview=False,
                    )
                    return

            lead = self.storage.get_lead_with_classification(lead_id)
            if lead is None:
                await event.respond(f"Лид #{lead_id} не найден.")
                return

            classification = None
            if lead.get("priority") is not None:
                from .llm_classifier import Classification as _C
                classification = _C(
                    is_match=bool(lead["is_match"]),
                    priority=int(lead["priority"]),
                    task_type=lead.get("task_type"),
                    estimated_budget=lead.get("estimated_budget"),
                    urgency=lead.get("urgency") or "unknown",
                    stack_match=tuple(lead.get("stack_match") or []),
                    summary=lead.get("summary"),
                    raw={}, model="", tokens_used=0,
                )

            await event.respond("Генерирую черновик...")
            draft = await self.drafter.generate(lead["text"], classification)
            if draft is None:
                await event.respond("Не удалось сгенерировать. Попробуй ещё раз.")
                return
            version = self.storage.save_draft(
                lead_id,
                body=draft.body, kb_doc_ids=draft.kb_doc_ids,
                model=draft.model, tokens_used=draft.tokens_used,
            )
            await event.respond(
                format_draft(lead_id, draft.body, version=version),
                parse_mode="html", link_preview=False,
            )

        @self.bot_client.on(events.NewMessage(pattern=r"^/test(?:\s+(.+))?"))
        async def test_filter(event: events.NewMessage.Event) -> None:
            text = event.pattern_match.group(1)
            if not text:
                await event.respond("Пришли так: /test нужен телеграм бот на Python")
                return

            result = match_text(text)
            if result.accepted:
                await event.respond(
                    f"Пройдет фильтр. Score: {result.score}. Совпало: {', '.join(result.matched_keywords)}"
                )
            else:
                reason = (
                    f"стоп-слова: {', '.join(result.rejected_by)}"
                    if result.rejected_by
                    else f"score ниже порога: {result.score}"
                )
                await event.respond(f"Не пройдет фильтр: {reason}")

    async def _register_source_handlers(self) -> list[tuple[Source, object]]:
        active: list[tuple[Source, object]] = []
        for source in self.sources:
            try:
                entity = await self.user_client.get_entity(source.handle)
            except (ValueError, RPCError) as exc:
                LOGGER.warning("Could not resolve %s: %s", source.handle, exc)
                continue

            active.append((source, entity))

            @self.user_client.on(events.NewMessage(chats=entity))
            async def on_message(event: events.NewMessage.Event, source: Source = source) -> None:
                await self._process_message(source, event.message)

        return active

    async def _catch_up(self, active_sources: Iterable[tuple[Source, object]]) -> None:
        buffered: list[tuple[datetime, Source, Message]] = []
        for source, entity in active_sources:
            try:
                async for message in self.user_client.iter_messages(entity, limit=self.config.catch_up_limit):
                    message_date = message.date or datetime.now(timezone.utc)
                    buffered.append((message_date, source, message))
            except RPCError as exc:
                LOGGER.warning("Could not catch up %s: %s", source.handle, exc)

        for _, source, message in sorted(buffered, key=lambda item: item[0]):
            await self._process_message(source, message)

    async def _process_message(self, source: Source, message: Message) -> None:
        text = message.message or ""
        if not text.strip():
            return

        match = match_text(text)
        if not match.accepted:
            return

        link = f"https://t.me/{source.username}/{message.id}"
        message_date = (message.date or datetime.now(timezone.utc)).isoformat()
        lead = LeadRecord(
            source=source.handle,
            message_id=int(message.id),
            link=link,
            text=text,
            score=match.score,
            keywords=match.matched_keywords,
            message_date=message_date,
        )

        if not self.storage.record_or_should_retry(lead):
            return

        subscribers = self.storage.subscribers()
        if not subscribers:
            LOGGER.warning("Lead matched, but no subscribers are configured yet: %s", link)
            return

        classification: Classification | None = None
        lead_id: int | None = None
        if self.classifier is not None:
            lead_id = self.storage.get_lead_id(lead.source, lead.message_id)
            classification = await self.classifier.classify(source.title, lead.text)
            if classification is not None and lead_id is not None:
                self.storage.save_classification(
                    lead_id,
                    is_match=classification.is_match,
                    priority=classification.priority,
                    task_type=classification.task_type,
                    estimated_budget=classification.estimated_budget,
                    urgency=classification.urgency,
                    stack_match=classification.stack_match,
                    summary=classification.summary,
                    raw=classification.raw,
                    model=classification.model,
                    tokens_used=classification.tokens_used,
                )

        body = format_lead(source, lead, classification=classification, lead_id=lead_id)
        delivered = False
        for chat_id in subscribers:
            try:
                await self.bot_client.send_message(chat_id, body, parse_mode="html", link_preview=False)
                delivered = True
            except RPCError as exc:
                LOGGER.warning("Could not deliver lead to %s: %s", chat_id, exc)

        if delivered:
            self.storage.mark_notified(lead.source, lead.message_id)
            LOGGER.info("Delivered lead from %s message %s", source.handle, message.id)

        if (
            delivered
            and self.drafter is not None
            and lead_id is not None
            and classification is not None
            and classification.is_match
            and classification.priority >= DRAFT_PRIORITY_THRESHOLD
        ):
            await self._auto_draft(lead_id, lead.text, classification, subscribers)

    async def _auto_draft(
        self,
        lead_id: int,
        lead_text: str,
        classification: Classification,
        subscribers: list[int],
    ) -> None:
        draft = await self.drafter.generate(lead_text, classification)
        if draft is None:
            LOGGER.warning("Draft generation returned empty for lead %s", lead_id)
            return
        version = self.storage.save_draft(
            lead_id,
            body=draft.body,
            kb_doc_ids=draft.kb_doc_ids,
            model=draft.model,
            tokens_used=draft.tokens_used,
        )
        body = format_draft(lead_id, draft.body, version=version)
        for chat_id in subscribers:
            try:
                await self.bot_client.send_message(chat_id, body, parse_mode="html", link_preview=False)
            except RPCError as exc:
                LOGGER.warning("Could not deliver draft to %s: %s", chat_id, exc)
        LOGGER.info("Drafted reply for lead %s (v%s, %s tokens)", lead_id, version, draft.tokens_used)

    async def _wait_until_stopped(self) -> None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass
        await stop_event.wait()


async def run_app() -> None:
    config = RuntimeConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = LeadBot(config)
    try:
        await app.run()
    finally:
        await app.shutdown()


def cli() -> None:
    parser = argparse.ArgumentParser(description="Monitor Telegram freelance sources and deliver leads.")
    parser.add_argument("--check-filter", help="Check a text against the current keyword filter.")
    args = parser.parse_args()

    if args.check_filter:
        result = match_text(args.check_filter)
        print(result)
        return

    asyncio.run(run_app())
