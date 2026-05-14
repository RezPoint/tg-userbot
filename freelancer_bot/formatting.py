from __future__ import annotations

import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import Button

from .sources import Source
from .storage import LeadRecord


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def lead_buttons(
    lead_id: int,
    has_draft: bool,
    link: str | None,
    status: str = "new",
    contact_username: str | None = None,
) -> list[list[Button]]:
    rows: list[list[Button]] = []
    row: list[Button] = []
    if has_draft:
        row.append(Button.inline("📄 Показать черновик", f"draft:{lead_id}".encode()))
        row.append(Button.inline("🔄 Перегенерировать", f"redraft:{lead_id}".encode()))
    else:
        row.append(Button.inline("✍️ Сгенерировать", f"draft:{lead_id}".encode()))
    rows.append(row)
    second = [Button.inline("❌ Скрыть", f"hide:{lead_id}".encode())]
    if link:
        second.insert(0, Button.url("🔗 Открыть пост", link))
    rows.append(second)
    rows.extend(status_buttons(lead_id, status, contact_username))
    return rows


def draft_buttons(
    lead_id: int,
    status: str = "drafted",
    contact_username: str | None = None,
) -> list[list[Button]]:
    rows: list[list[Button]] = [[
        Button.inline("🔄 Ещё вариант", f"redraft:{lead_id}".encode()),
        Button.inline("❌ Скрыть", f"hidedraft:{lead_id}".encode()),
    ]]
    rows.extend(status_buttons(lead_id, status, contact_username))
    return rows


BTN_STATUS = "📊 Статус"
BTN_SOURCES = "📡 Источники"
BTN_KEYWORDS = "🔑 Ключи"
BTN_FUNNEL = "📋 Воронка"
BTN_STOP = "🔕 Отписаться"

STATUS_LABELS = {
    "new": "🆕 новый",
    "drafted": "✍️ черновик",
    "sent": "📤 отправлен",
    "replied": "💬 ответили",
    "won": "🏆 выиграл",
    "lost": "💔 проиграл",
    "skipped": "⏭ пропущен",
}


def reply_keyboard() -> list[list[Button]]:
    return [
        [Button.text(BTN_STATUS, resize=True), Button.text(BTN_FUNNEL, resize=True)],
        [Button.text(BTN_SOURCES, resize=True), Button.text(BTN_KEYWORDS, resize=True)],
        [Button.text(BTN_STOP, resize=True)],
    ]


def status_buttons(lead_id: int, status: str, contact_username: str | None) -> list[list[Button]]:
    rows: list[list[Button]] = []
    if contact_username:
        rows.append([Button.url(f"💬 Чат с @{contact_username}", f"https://t.me/{contact_username}")])
    if status in ("new", "drafted"):
        rows.append([
            Button.inline("✅ Отправлено", f"status:sent:{lead_id}".encode()),
            Button.inline("⏭ Пропустить", f"status:skipped:{lead_id}".encode()),
        ])
    elif status == "sent":
        rows.append([
            Button.inline("💬 Получил ответ", f"status:replied:{lead_id}".encode()),
            Button.inline("💔 Проиграл", f"status:lost:{lead_id}".encode()),
        ])
    elif status == "replied":
        rows.append([
            Button.inline("🏆 Выиграл", f"status:won:{lead_id}".encode()),
            Button.inline("💔 Проиграл", f"status:lost:{lead_id}".encode()),
        ])
    return rows


def format_funnel(items: list[dict], counts: dict[str, int]) -> str:
    if not items:
        active = counts.get("drafted", 0) + counts.get("sent", 0) + counts.get("replied", 0)
        if active == 0:
            return "<b>📋 Воронка пуста</b>\n\nКак появятся высокоприоритетные лиды — попадут сюда."
        return f"<b>📋 Воронка пуста</b> (всего активных: {active})"

    summary_parts = []
    for status, label in [("drafted", "черновик"), ("sent", "отправлено"), ("replied", "ответили")]:
        cnt = counts.get(status, 0)
        if cnt:
            summary_parts.append(f"{label}: {cnt}")
    summary = " · ".join(summary_parts) if summary_parts else "—"

    lines = [f"<b>📋 Воронка</b> · {summary}\n"]
    for it in items:
        label = STATUS_LABELS.get(it["status"], it["status"])
        pri = f" · p{it['priority']}" if it.get("priority") is not None else ""
        contact = f" · @{it['contact_username']}" if it.get("contact_username") else ""
        summary_text = (it.get("summary") or it.get("task_type") or "—")[:60]
        lines.append(
            f"<code>#{it['id']}</code> · {label}{pri}{contact}\n  {html.escape(summary_text)}"
        )
    return "\n".join(lines)


def format_draft(lead_id: int, body: str, version: int = 1) -> str:
    return (
        f"<b>✍️ Черновик отклика</b> · лид <code>#{lead_id}</code> · v{version}\n\n"
        f"<blockquote>{html.escape(body)}</blockquote>"
    )


def format_inbound(
    lead_id: int,
    username: str,
    text: str,
    is_new_reply: bool,
) -> str:
    header = "💬 <b>Заказчик ответил</b>" if is_new_reply else "💬 <b>Новое сообщение</b>"
    preview = truncate(text, limit=600) if text else "(вложение без текста)"
    return (
        f"{header} · лид <code>#{lead_id}</code>\n"
        f"<b>От:</b> @{html.escape(username)}\n\n"
        f"<blockquote>{html.escape(preview)}</blockquote>"
    )


def format_reply_draft(lead_id: int, body: str, version: int = 1) -> str:
    return (
        f"<b>💭 Черновик ответа</b> · лид <code>#{lead_id}</code> · v{version}\n\n"
        f"<pre>{html.escape(body)}</pre>"
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


USERNAME_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{4,31})")


def extract_telegram_username(text: str, exclude: set[str] | None = None) -> str | None:
    exclude_lc = {x.lstrip("@").lower() for x in (exclude or set())}
    for m in USERNAME_RE.finditer(text):
        uname = m.group(1)
        if uname.lower() in exclude_lc:
            continue
        if uname.lower().endswith("bot"):
            continue
        return uname
    return None


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

