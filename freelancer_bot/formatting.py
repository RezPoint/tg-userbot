from __future__ import annotations

import html
import re
from datetime import datetime

from .sources import Source
from .storage import LeadRecord


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


def format_lead(source: Source, lead: LeadRecord) -> str:
    contacts = extract_contacts(lead.text)
    contact_line = ", ".join(html.escape(item) for item in contacts) if contacts else "не найдены"
    keywords = ", ".join(html.escape(item) for item in lead.keywords[:8])
    date_text = lead.message_date
    try:
        date_text = datetime.fromisoformat(lead.message_date).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass

    link_line = f'\n<a href="{html.escape(lead.link)}">Открыть пост</a>' if lead.link else ""
    excerpt = html.escape(truncate(lead.text))

    return (
        f"<b>Новый лид</b> · score {lead.score}\n"
        f"<b>Источник:</b> {html.escape(source.title)} ({html.escape(source.handle)})\n"
        f"<b>Дата:</b> {html.escape(date_text)}\n"
        f"<b>Совпало:</b> {keywords}\n"
        f"<b>Контакты:</b> {contact_line}"
        f"{link_line}\n\n"
        f"{excerpt}"
    )

