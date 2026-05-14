from __future__ import annotations

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .sources import Source
from .storage import LeadRecord


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def format_draft(lead_id: int, body: str, version: int = 1) -> str:
    return (
        f"<b>✍️ Черновик отклика</b> · лид <code>#{lead_id}</code> · v{version}\n\n"
        f"<blockquote>{html.escape(body)}</blockquote>\n\n"
        f"Перегенерировать: <code>/redraft {lead_id}</code>"
    )


CONTACT_RE = re.compile(
    r"(?P<username>@[A-Za-z0-9_]{5,32})|(?P<email>[\w.+-]+@[\w-]+\.[\w.-]+)|(?P<url>https?://\S+)"
)


def truncate(text: str, limit: int = 1600) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def extract_contacts(text: str) -> tuple[str, ...]:
    contacts: list[str] = []
    for match in CONTACT_RE.finditer(text):
        value = match.group(0).rstrip(".,;)")
        if value not in contacts:
            contacts.append(value)
    return tuple(contacts[:8])


def format_lead(source: Source, lead: LeadRecord, classification=None, lead_id: int | None = None) -> str:
    contacts = extract_contacts(lead.text)
    contact_line = ", ".join(html.escape(item) for item in contacts) if contacts else "не найдены"
    keywords = ", ".join(html.escape(item) for item in lead.keywords[:8])
    date_text = lead.message_date
    try:
        parsed = datetime.fromisoformat(lead.message_date)
        date_text = parsed.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M МСК")
    except ValueError:
        pass

    link_line = f'\n<a href="{html.escape(lead.link)}">Открыть пост</a>' if lead.link else ""
    excerpt = html.escape(truncate(lead.text))

    llm_block = ""
    header_emoji = "💼"
    if classification is not None:
        match_emoji = "✅" if classification.is_match else "❌"
        budget = (
            f"{classification.estimated_budget:,} ₽".replace(",", " ")
            if classification.estimated_budget is not None
            else "не указан"
        )
        urgency_map = {"low": "🟢 низкая", "medium": "🟡 средняя", "high": "🔴 высокая", "unknown": "—"}
        stack = ", ".join(html.escape(s) for s in classification.stack_match) if classification.stack_match else "—"
        summary = html.escape(classification.summary) if classification.summary else "—"
        header_emoji = "🎯" if classification.priority >= 7 else ("💼" if classification.priority >= 4 else "📭")
        llm_block = (
            f"<b>{match_emoji} LLM:</b> приоритет <b>{classification.priority}/10</b> · "
            f"{html.escape(classification.task_type or '—')}\n"
            f"<b>💰 Бюджет:</b> {budget} · <b>⚡ Срочность:</b> {urgency_map.get(classification.urgency, '—')}\n"
            f"<b>🛠 Стек:</b> {stack}\n"
            f"<b>📝 Резюме:</b> {summary}\n\n"
        )

    id_suffix = f" · <code>#{lead_id}</code>" if lead_id is not None else ""
    return (
        f"<b>{header_emoji} Новый лид</b> · keyword-score {lead.score}{id_suffix}\n"
        f"<b>Источник:</b> {html.escape(source.title)} ({html.escape(source.handle)})\n"
        f"<b>Дата:</b> {html.escape(date_text)}\n"
        f"{llm_block}"
        f"<b>Совпало:</b> {keywords}\n"
        f"<b>Контакты:</b> {contact_line}"
        f"{link_line}\n\n"
        f"{excerpt}"
    )

