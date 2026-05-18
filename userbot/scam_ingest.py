"""Парсер скам-каналов → запись в skibidi.scam_blacklist / scam_credentials.

Подписывается на список каналов из env SKIBIDI_SCAM_INGEST_CHANNELS (через запятую),
для каждого нового сообщения извлекает Telegram-username скамеров, телефоны,
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
PHONE_RE = re.compile(r"(?<!\d)(?:(?:\+?7|8)[\s\-(]*)?9\d{2}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
BYBIT_UID_RE = re.compile(r"bybit\.com/[^/]+/p2p/profile/(s[0-9a-f]+)", re.IGNORECASE)
MEXC_UID_RE = re.compile(r"mexc\.com/[^/]+/p2p/profile/(\w+)", re.IGNORECASE)
# «⏺️ Реквизит: 9203041024» или «Реквизит: 9203041024» — 10–12 цифр без BIN.
ACCOUNT_SHORT_RE = re.compile(r"[Рр]еквизит[ы]?\s*:?\s*(\d{10,12})(?!\d)")
# «🆔 UID: 502905147» — числовой Bybit/MEXC UID
BYBIT_UID_NUM_RE = re.compile(r"(?m)^[^A-Za-zА-Яа-я0-9\n]*UID[\s:*]*(\d{6,12})(?!\d)", re.IGNORECASE)
# «🪪Никнейм: Matvey_BB» — отображаемое имя на Bybit (минимум 4 символа)
BYBIT_NICK_RE = re.compile(r"(?m)^[^A-Za-zА-Яа-я\n]*[Нн]ик(?:нейм)?[\s:*]*([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_.\-]{2,32})")
# «Bombilooo (UID 576902331)» — никнейм с числовым UID в скобках (мин 4)
BYBIT_NICK_UID_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.-]{3,32})\s*\(\s*UID\s+(\d{6,12})\s*\)",
    re.IGNORECASE,
)
# auto-сгенерированный Bybit-username вида «User1234ABCD» или просто «User» как остаток
BYBIT_AUTO_USER_RE = re.compile(r"\bUser[0-9A-Za-z]{6,12}\b")
# одиночный «User» — остаток шаблона «User @xxx» после чистки @
BARE_USER_RE = re.compile(r"\bUser\b")
# bare URL без протокола: «dammisa396.com»
BARE_URL_RE = re.compile(r"\b[\w-]+\.(?:com|ru|net|org|io|app|me|xyz|info|biz|co|cc|club|store|site|online|tech)\b", re.IGNORECASE)
# ФИО: имя ≥3 символов, далее ≥1 слово ≥3 символов или инициал (одна заглавная с/без точки)
_WORD = r"[А-ЯЁа-яёa-zA-Z\-]{2,}"  # хвост слова после первой буквы (≥2 → итого ≥3)
_INITIAL = r"\.?"  # одна буква, опционально с точкой
FULL_NAME_RE = re.compile(
    rf"\b([А-ЯЁA-Z]{_WORD}(?:\s+[А-ЯЁA-Z](?:{_WORD}|{_INITIAL}))(?:\s+[А-ЯЁA-Z](?:{_WORD}|{_INITIAL})){{0,3}})\b",
)

# Слова, наличие которых означает «это не ФИО, а кусок жалобы CAPS»
NAME_STOPWORDS = frozenset({
    "ОТПРАВЛЯЕТ", "ОТПРАВЛЯЮ", "ОТКАЗЫВАЕТСЯ", "ОТКАЗЫАЕТСЯ", "ИГНОРИТ", "ИГНОРИРУЕТ",
    "ОТКАЗ", "СКАМ", "БАН", "СКАМЩИКА", "СКАМЩИК", "ВОПРОС", "ГРЯЗЬ", "СРАЗУ",
    "РАБОТАТЬ", "ВЕРНУТЬ", "ОТДАТЬ", "ПЛАТИТЬ", "ПРИНЯТЬ", "ОТМЕНИТЬ", "ОТПРАВКА",
    "РЕШАТЬ", "РЕШИТЬ", "СДЕЛАТЬ", "СДЕЛАЛ", "СКИДЫВАЕТ", "КИДАЕТ", "ВЫДАЛ",
    "СКРЫВАЕТ", "СКРЫЛ", "ПРОПАЛ", "ИСЧЕЗ", "ТРЕБУЕТ",
    # Глаголы/слова из жалоб
    "СМЕНИЛ", "СМЕНИЛА", "СМЕНИЛИ", "НОМЕР", "НОМЕРА", "АПИЛ", "АПЕЛЛЯЦИЯ",
    "ЛИЦА", "ЛИЦО", "ГОВНОЕДА", "ГОВНОЕД", "СИДИТ", "СИДЯТ", "БУДЬТЕ",
    "ВНИМАТЕЛЬНЫ", "ВНИМАНИЕ", "ВНИМАТЕЛЕН",
    # Label'ы секций («Данные», «Телефон», «Карт[аыуоие]?а»…)
    "ДАННЫЕ", "ДАННЫХ", "ТЕЛЕФОН", "ТЕЛЕФОНЫ", "КАРТА", "КАРТЫ", "СЧЁТ", "СЧЕТ",
    "EMAIL", "ПОЧТА", "БАНК", "АДРЕС", "ИМЯ", "ФАМИЛИЯ", "ОТЧЕСТВО",
    "КОНТАКТ", "КОНТАКТЫ", "СУММА", "РЕКВИЗИТЫ", "ПРИЧИНА", "ИНФО", "ИНФОРМАЦИЯ",
    # Английский мусор из ников
    "COUNTER", "TERRORIST", "WIN", "USER", "BOT", "TELEGRAM", "BYBIT", "MEXC",
    # Названия банков (если попадают как первое слово «ФИО»)
    "СБЕРБАНК", "СБЕР", "ВТБ", "ГАЗПРОМБАНК", "АЛЬФА", "АЛЬФА-БАНК",
    "РОССЕЛЬХОЗБАНК", "РСХБ", "ТИНЬКОФФ", "Т-БАНК", "TINKOFF",
    "РАЙФФАЙЗЕН", "RAIFFEISEN", "ОТКРЫТИЕ", "ПРОМСВЯЗЬБАНК", "ПСБ",
    "МКБ", "СОВКОМБАНК", "РОСБАНК", "ОТП", "ЮНИКРЕДИТ", "CITIBANK",
    "АК", "БАРС", "УРАЛСИБ", "ЗЕНИТ", "АВАНГАРД", "ТОЧКА",
    "ДОМ.РФ", "ПОЧТА", "ПОЧТА-БАНК", "ХОУМ", "КРЕДИТ", "EUROPE",
    "СТАНДАРТ", "ЛОКО", "ЛОКО-БАНК", "УБРИР", "СКБ", "БКС", "ФИНАМ", "ИНТЕЗА",
    "OZON", "ОЗОН", "WILDBERRIES", "WB", "ЯНДЕКС", "YANDEX",
    "ЮMONEY", "YOOMONEY", "QIWI", "КИВИ", "PAYPAL",
    "SBP", "СБП",
})
# Полный список российских банков (топ-100 + payment services). Для:
# (а) исключения из FIO (если value начинается с банка — это лейбл, не имя)
# (б) детекции как отдельный credential kind='bank'
RUSSIAN_BANKS_NORMALIZED: list[tuple[str, str]] = [
    (r"СберБанк|Сбербанк[еау]?|Сбербанку|Сбер(?:ыч|ом|у|а|е)?|Sber(?:Bank)?|СБ\b|Sb\b", "Сбербанк"),
    (r"ВТБ(?:24|шка|шку|шке)?|VTB", "ВТБ"),
    (r"Газпромбанк[еу]?|Газпром[-\s]?банк|Газпром\b|Gazprombank|ГПБ\b", "Газпромбанк"),
    (r"Альфа[-\s]?Банк|Альфабанк|Альфа\b|Alfa[-\s]?Bank", "Альфа-Банк"),
    (r"Россельхоз(?:банк|банке|банку)?|РСХБ|Россельх", "Россельхозбанк"),
    (r"Тинькофф(?:банк|банке|банку)?|Тинькоф|Тинь(?:ка|ке|ку|ок|ёк)?|Тинёк|Т[-\s]?Банк|Tinkoff|T[-\s]?Bank|T[-\s]?Card|ТБ\b", "Тинькофф"),
    (r"Райффайзен(?:банк|банке|банку)?|Райф(?:фа)?|Raiffeisen", "Райффайзен"),
    (r"Открытие\s*банк|Открытие\b|ФК\s*Открытие|Otkrytie", "Открытие"),
    (r"Промсвязьбанк[еу]?|Промсвязь|ПСБ\b|PSB\b", "ПСБ"),
    (r"МКБ\b|Московский\s*Кредитный\s*Банк", "МКБ"),
    (r"Совкомбанк[еу]?|Совком|Sovcombank", "Совкомбанк"),
    (r"Росбанк[еу]?|Rosbank", "Росбанк"),
    (r"ОТП\s*Банк|ОТП\b|OTP\s*Bank|OTP\b", "ОТП Банк"),
    (r"ЮниКредит(?:Банк)?|UniCredit", "ЮниКредит"),
    (r"Citi(?:bank)?|Сити[-\s]?банк", "Ситибанк"),
    (r"АК\s*Барс|Ак\s*Барс|АкБарс", "Ак Барс"),
    (r"Уралсиб|Uralsib", "Уралсиб"),
    (r"Зенит\b|Zenit", "Зенит"),
    (r"Авангард[еу]?|Аванг\b", "Авангард"),
    (r"Точка\s*Банк|Точка\b|Tochka", "Точка"),
    (r"ДОМ\.?РФ|Дом\.РФ|Дом\.рф|DomRF", "ДОМ.РФ"),
    (r"Почта[-\s]?Банк|Pochta\s*Bank", "Почта Банк"),
    (r"Хоум\s*Кредит|Хоум\b|ХК\b|Home\s*Credit", "Хоум Кредит"),
    (r"Кредит\s*Европа\s*Банк|КЕБ\b", "Кредит Европа Банк"),
    (r"Русский\s*Стандарт|Russian\s*Standard", "Русский Стандарт"),
    (r"Локо[-\s]?Банк|Локо\b", "Локо-Банк"),
    (r"УБРиР|УБР\b|Уральский\s*Банк\s*Реконструкции", "УБРиР"),
    (r"СКБ[-\s]?банк|СКБ\b", "СКБ-Банк"),
    (r"БКС\s*Банк|БКС\b|BCS\s*Bank", "БКС"),
    (r"Финам[еу]?", "Финам"),
    (r"Интеза|Intesa", "Интеза"),
    (r"Кубань\s*Кредит", "Кубань Кредит"),
    (r"OZON\s*Банк|Озон\s*Банк|Озон[-\s]?Счёт|Озон[-\s]?Карт[аыуоие]?|Ozon\s*Bank|Ozon[-\s]?Card", "OZON Банк"),
    (r"Еком\s*Банк|EcomBank|Ecom\s*Bank", "Еком Банк"),
    (r"Wildberries\s*Банк|WB\s*Банк|WB[-\s]?Карт[аыуоие]?", "WB Банк"),
    (r"Яндекс\s*Банк|Яндекс[-\s]?Карт[аыуоие]?|Yandex\s*Bank|Yandex[-\s]?Pay|YBS", "Яндекс Банк"),
    (r"X5\s*Банк", "X5 Банк"),
    (r"Black\s*Card|Black\s*Карта", "Black Card (Тинькофф)"),
    (r"Premium\s*Card", "Premium Card"),
    (r"ЮMoney|Юmoney|Yoomoney|Яндекс\.Деньги", "ЮMoney"),
    (r"QIWI|Киви[-\s]?(?:Банк)?", "QIWI"),
    (r"SBP|СБП", "СБП"),
    (r"PayPal", "PayPal"),
    (r"Юпей|Yandex\s*Pay", "Yandex Pay"),
    (r"СберПрайм\+?", "СберПрайм"),
]


def normalize_bank(value: str) -> str | None:
    """Возвращает каноническое имя банка или None если не банк."""
    v = value.strip()
    for pattern, canonical in RUSSIAN_BANKS_NORMALIZED:
        if re.fullmatch(pattern, v, re.IGNORECASE):
            return canonical
    return None


RUSSIAN_BANKS_PATTERNS = [
    # Топ-10 + сленг/сокращения
    r"СберБанк", r"Сбербанк", r"Сбербанке", r"Сбербанку", r"Сбер(?:ыч|ом|у|а|е)?",
    r"Sber(?:Bank)?", r"СБ\b", r"Sb\b",
    r"ВТБ(?:24|шка|шку|шке)?", r"VTB",
    r"Газпромбанк(?:е|у)?", r"Газпром[-\s]?банк", r"Газпром\b", r"Gazprombank", r"ГПБ\b",
    r"Альфа[-\s]?Банк", r"Альфабанк", r"Альфа\b", r"Alfa[-\s]?Bank",
    r"Россельхоз(?:банк|банке|банку)?", r"РСХБ", r"Россельх",
    r"Тинькофф(?:банк|банке|банку)?", r"Тинькоф", r"Тинь(?:ка|ке|ку|ок|ёк)?", r"Тинёк",
    r"Т[-\s]?Банк", r"Tinkoff", r"T[-\s]?Bank", r"T[-\s]?Card", r"ТБ\b",
    r"Райффайзен(?:банк|банке|банку)?", r"Райф(?:фа)?", r"Raiffeisen",
    r"Открытие\s*банк", r"Открытие\b", r"ФК\s*Открытие", r"Otkrytie",
    r"Промсвязьбанк(?:е|у)?", r"Промсвязь", r"ПСБ\b", r"PSB\b",
    r"МКБ\b", r"Московский\s*Кредитный\s*Банк",
    # Средние + сокращения
    r"Совкомбанк(?:е|у)?", r"Совком", r"Sovcombank",
    r"Росбанк(?:е|у)?", r"Rosbank",
    r"ОТП\s*Банк", r"ОТП\b", r"OTP\s*Bank", r"OTP\b",
    r"ЮниКредит(?:Банк)?", r"UniCredit",
    r"Citi(?:bank)?", r"Сити[-\s]?банк",
    r"АК\s*Барс", r"Ак\s*Барс", r"АкБарс",
    r"Уралсиб", r"Uralsib",
    r"Зенит\b", r"Zenit",
    r"Авангард(?:е|у)?", r"Аванг\b",
    r"Точка\s*Банк", r"Точка\b", r"Tochka",
    r"ДОМ\.?РФ", r"Дом\.РФ", r"Дом\.рф", r"DomRF",
    r"Почта[-\s]?Банк", r"Pochta\s*Bank",
    r"Хоум\s*Кредит", r"Хоум\b", r"ХК\b", r"Home\s*Credit",
    r"Кредит\s*Европа\s*Банк", r"КЕБ\b",
    r"Русский\s*Стандарт", r"Russian\s*Standard",
    r"Локо[-\s]?Банк", r"Локо\b",
    r"УБРиР", r"УБР\b", r"Уральский\s*Банк\s*Реконструкции",
    r"СКБ[-\s]?банк", r"СКБ\b",
    r"БКС\s*Банк", r"БКС\b", r"BCS\s*Bank",
    r"Финам(?:е|у)?",
    r"Интеза", r"Intesa",
    r"Кубань\s*Кредит",
    r"Россия\s*Банк", r"АБ\s*Россия",
    # Цифровые / новые + сокращения
    r"OZON\s*Банк", r"Озон\s*Банк", r"Озон[-\s]?Карт[аыуоие]?", r"Ozon\s*Bank", r"Ozon[-\s]?Card",
    r"Wildberries\s*Банк", r"WB\s*Банк", r"WB[-\s]?Карт[аыуоие]?",
    r"Яндекс\s*Банк", r"Яндекс[-\s]?Карт[аыуоие]?", r"Yandex\s*Bank", r"Yandex[-\s]?Pay", r"YBS",
    r"X5\s*Банк",
    # Карт[аыуоие]?очные продукты часто упоминаются как «банк»
    r"Black\s*Card", r"Black\s*Карт[аыуоие]?а", r"Premium\s*Card",
    # Платёжные сервисы
    r"ЮMoney", r"Юmoney", r"Yoomoney", r"Яндекс\.Деньги",
    r"QIWI", r"Киви[-\s]?(?:Банк)?",
    r"SBP", r"СБП",
    r"PayPal", r"Юпей", r"Yandex\s*Pay",
    r"СберПрайм", r"СберПрайм\+",
    # Региональные / прочие
    r"АК[-\s]?Барс", r"Кубань", r"Снежинский",
]
BANK_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я])(" + "|".join(RUSSIAN_BANKS_PATTERNS) + r")(?![A-Za-zА-Яа-я])",
    re.IGNORECASE,
)

USERNAME_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
# Строка отправителя: с @username, или с произвольным значением после двоеточия
REPORTER_LINE_RE = re.compile(
    r"(?:🗣|🥷)?\s*(?:[Оо]тправитель|[Пп]рислал|[Ии]сточник|[Аа]втор)\s*:?\s*[^\n]*",
)
REPORTER_USERNAME_RE = re.compile(
    r"(?:🗣|🥷)?\s*(?:[Оо]тправитель|[Пп]рислал|[Ии]сточник|[Аа]втор)\s*:?\s*@([A-Za-z0-9_]+)",
)
# Свободно-текстовое значение «Отправитель: Counter Terrorist Win» (не @username)
REPORTER_FREE_VALUE_RE = re.compile(
    r"(?:🗣|🥷)?\s*(?:[Оо]тправитель|[Пп]рислал|[Ии]сточник|[Аа]втор)\s*:?\s*([^\n@]+)",
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]+\)")
DIGITS_RE = re.compile(r"\d+(?:[\s\-.]*\d+)*")


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
    # РФ без префикса: 9XX XXX XX XX → 7 9XX XXX XX XX
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
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
    account_shorts: list[str]
    bybit_nicknames: list[str]
    bybit_uids_numeric: list[str]
    full_names: list[str]
    banks: list[str]


EXAMPLE_MARKERS = (
    "🏳 пример", "🏳пример", "пример:", "example:", "тестовый пост",
)


def is_example_post(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in EXAMPLE_MARKERS)


def extract(text: str) -> Extracted:
    raw = text or ""
    # Служебные «пример»-посты из канала-источника игнорируем целиком
    if is_example_post(raw):
        return Extracted([], [], [], [], [], [], [], [], [], [])
    reporters = {m.group(1).lower() for m in REPORTER_USERNAME_RE.finditer(raw)}
    clean = REPORTER_LINE_RE.sub("", raw)
    # Вырезаем URL'ы целиком — оттуда регексы CARD/PHONE/ACCOUNT ловят order_id как фейк-карту/телефон
    clean = re.sub(r"https?://\S+", "", clean)
    # Хэштеги тоже не должны попадать в FIO/nickname
    clean = re.sub(r"#[\wЀ-ӿ]+", "", clean)
    usernames = []
    seen_u: set[str] = set()
    for m in USERNAME_RE.finditer(clean):
        u = m.group(1)
        low = u.lower()
        if low.endswith("_bot") or low in seen_u or low in reporters:
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

    # 10-значные «реквизиты»; отсекаем если это просто хвост уже распознанного телефона/карты
    phone_tails = {p.lstrip("+")[-10:] for p in phones}
    card_tails = {c[-10:] for c in cards}
    accs, seen_a = [], set()
    for m in ACCOUNT_SHORT_RE.finditer(clean):
        v = m.group(1)
        if v in phone_tails or v in card_tails:
            continue
        if v in seen_a:
            continue
        seen_a.add(v); accs.append(v)

    nicks, seen_n = [], set()
    for m in BYBIT_NICK_RE.finditer(raw):
        v = m.group(1).strip()
        if v in {"—", "-", "?"} or v.lower() in seen_n:
            continue
        seen_n.add(v.lower())
        nicks.append(v)
    # User<hash> — это автогенерированный placeholder Bybit, не реальный ник скаммера. Не сохраняем.

    uids_num, seen_un = [], set()
    for m in BYBIT_UID_NUM_RE.finditer(raw):
        v = m.group(1)
        if v not in seen_un:
            seen_un.add(v); uids_num.append(v)
    # «Bombilooo (UID 576902331)» — пара ник+UID
    for m in BYBIT_NICK_UID_RE.finditer(raw):
        nick_v, uid_v = m.group(1), m.group(2)
        if nick_v.lower() not in seen_n:
            seen_n.add(nick_v.lower()); nicks.append(nick_v)
        if uid_v not in seen_un:
            seen_un.add(uid_v); uids_num.append(uid_v)

    # Банки — отдельный credential kind, нормализуем к каноническому имени
    banks: list[str] = []
    seen_bk: set[str] = set()
    for m in BANK_RE.finditer(clean):
        raw_v = m.group(1).strip()
        canon = normalize_bank(raw_v) or raw_v
        low = canon.lower()
        if low and low not in seen_bk:
            seen_bk.add(low)
            banks.append(canon)

    # Извлекаем свободные значения отправителя (для blacklist FIO-кандидатов)
    sender_free = set()
    for m in REPORTER_FREE_VALUE_RE.finditer(raw):
        val = m.group(1).strip().lower()
        if val:
            sender_free.add(val)
    # ФИО — ищем в очищенном тексте (без отправителя, ссылок и эмодзи-блоков)
    full_clean = REPORTER_LINE_RE.sub("", raw)
    full_clean = re.sub(r"https?://\S+", "", full_clean)
    full_clean = re.sub(r"#[\wЀ-ӿ]+", "", full_clean)
    full_clean = re.sub(r"[🆔🪪⏺️⭐🥷*️⃣][^\n]*", "", full_clean)
    full_clean = BYBIT_AUTO_USER_RE.sub("", full_clean)
    names, seen_fn = [], set()
    for m in FULL_NAME_RE.finditer(full_clean):
        v = m.group(1).strip()
        low = v.lower()
        if low in seen_fn or low in {"скам в p2p", "скам ный", "скам в bybit"}:
            continue
        # совпало со значением отправителя («Counter Terrorist Win») — это не ФИО
        if low in sender_free:
            continue
        # отсечь явный мусор — слово «Скам» / «Скамер» / «Bybit» в начале
        first = v.split()[0].lower()
        if first in {"скам", "скамер", "скаммер", "bybit", "mexc", "telegram", "user"}:
            continue
        # Если в matched value есть стоп-слово после первых 2+ слов — обрезаем до него
        words = v.split()
        cut_at = None
        for i, w in enumerate(words):
            if w.upper() in NAME_STOPWORDS:
                cut_at = i
                break
        if cut_at is not None:
            if cut_at < 2:
                continue  # стоп-слово в начале — всё ФИО мусорное
            v = " ".join(words[:cut_at])
            low = v.lower()
            if low in seen_fn:
                continue
        # отсечь явный мусор: слишком длинный (> 60 символов) или содержит подряд > 3 пробелов
        if len(v) > 60 or "    " in v:
            continue
        # мульти-строчное "имя" — почти всегда заголовок секции, не ФИО
        if "\n" in v:
            continue
        # одиночные буквы без точки на хвосте — предлоги/союзы, не инициалы (Треугольник С, Иван В)
        toks = v.split()
        if len(toks) >= 2 and len(toks[-1]) == 1 and toks[-1].upper() in {"С","В","К","А","И","О","У","Я","Б"}:
            continue
        seen_fn.add(low)
        names.append(v)

    return Extracted(
        usernames=usernames, phones=phones, cards=cards,
        bybit_uids=bybit, mexc_uids=mexc, account_shorts=accs,
        bybit_nicknames=nicks, bybit_uids_numeric=uids_num,
        full_names=names, banks=banks,
    )


import time as _time

# Кэш в памяти процесса: username.lower() -> (resolved_id_or_None, skip_until_monotonic)
# skip_until > now() означает «не дёргать API до этого момента» (был флуд или dead-username).
_RESOLVE_CACHE: dict[str, tuple[int | None, float]] = {}
# Глобальный флуд-таймстамп: если последний resolve упал во флуд, все последующие
# пропускаются мгновенно до этого времени (Telegram-флуд per-account, не per-username).
_GLOBAL_FLOOD_UNTIL: float = 0.0


async def _resolve(client: TelegramClient, username: str) -> int | None:
    global _GLOBAL_FLOOD_UNTIL
    now = _time.monotonic()
    key = username.lower()
    cached = _RESOLVE_CACHE.get(key)
    if cached is not None:
        cached_id, until = cached
        # Если есть ID — возвращаем сразу. Если None и таймаут не истёк — пропускаем.
        if cached_id is not None or now < until:
            return cached_id
    if now < _GLOBAL_FLOOD_UNTIL:
        # Аккаунт во флуде — не дёргаем API, помечаем в кэше до конца флуда
        _RESOLVE_CACHE[key] = (None, _GLOBAL_FLOOD_UNTIL)
        return None
    try:
        ent = await client.get_entity(username)
        resolved_id = getattr(ent, "id", None)
        _RESOLVE_CACHE[key] = (resolved_id, now + 86400)  # кэшируем успех на сутки
        return resolved_id
    except (UsernameInvalidError, UsernameNotOccupiedError, ValueError):
        # Юзера нет — кэшируем None надолго чтобы больше не дёргать
        _RESOLVE_CACHE[key] = (None, now + 86400)
        return None
    except FloodWaitError as e:
        LOG.warning("flood wait %ss on resolve @%s — глобальный пропуск резолвов", e.seconds, username)
        _GLOBAL_FLOOD_UNTIL = now + e.seconds
        _RESOLVE_CACHE[key] = (None, _GLOBAL_FLOOD_UNTIL)
        return None
    except Exception as e:  # noqa: BLE001
        LOG.warning("resolve @%s failed: %s", username, e)
        _RESOLVE_CACHE[key] = (None, now + 3600)
        return None


def _post_url(channel_username: str, msg_id: int) -> str:
    return f"https://t.me/{channel_username}/{msg_id}"


def _summary_from_post(text: str, also_strip: list[str] | None = None) -> str:
    cleaned = REPORTER_LINE_RE.sub("", text or "")
    for token in (also_strip or []):
        if token and len(token) >= 3:
            cleaned = re.sub(rf"\b{re.escape(token)}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = MARKDOWN_LINK_RE.sub("", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"@[A-Za-z0-9_]+", "", cleaned)
    # Хэштеги
    cleaned = re.sub(r"#[\wЀ-ӿ]+", "", cleaned)
    # Аналитические-отчёты по номеру (типа `🔎 Телефонные книги:`, `🧑‍💻 ВКонтакте:`, `🏦 Мобильный банк:`)
    cleaned = re.sub(
        r"(?m)^[^\nА-Яа-яA-Za-z0-9]*(?:Телефонные\s*книги|Мобильный\s*банк|Оператор|Регион|Страна|Интересовались\s*этим|E-?mail|ВКонтакте|Telegram\s*ID|VK|Skype|Instagram|Facebook)\s*:?[^\n]*",
        "", cleaned, flags=re.IGNORECASE,
    )
    # Графика-разделители из BoxDrawing (├ │ └ ─ ━ ┌ ┐ ┘)
    cleaned = re.sub(r"[├│└┌┐┘┃─━═▬]+", "", cleaned)
    # Внимание: emoji *️⃣ содержит ASCII '*' в charset — обрабатываем отдельно.
    cleaned = re.sub(r"[🆔🪪⏺️⭐🥷🔎🧑🏦📧👁📱💻📞✉️🖥️🎁][^\n]*", "", cleaned)
    cleaned = re.sub(r"\*️⃣[^\n]*", "", cleaned)
    # Шаблонные лейблы P2P_BlackList — ловим только в начале строки, чтобы не съесть «ник» из «мошенник»
    cleaned = re.sub(
        r"(?m)^\s*(?:Telegram\s+(?:ID|Username)|Биржа|UID|Никнейм|Никн?:|Ник|Тг|Tg|Телеграм|Реквизит[ы]?|Реки|Юзернейм|Username|HTX(?:\s+INFO)?|Bybit|MEXC|Binance|Huobi|OKX|TradeCode|by\s+TradeCode)\b\s*:?[^\n]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Названия банков (нормализованные и сырые) — вырезаем из reason, они уже в реквизитах
    cleaned = BANK_RE.sub("", cleaned)
    # Auto-Bybit юзернеймы (User0323G8HKHt) и одиночный «User»
    cleaned = BYBIT_AUTO_USER_RE.sub("", cleaned)
    cleaned = BARE_USER_RE.sub("", cleaned)
    cleaned = BARE_URL_RE.sub("", cleaned)
    # Вычищаем длинные цифровые последовательности в одной строке (телефоны, карты, UID).
    cleaned = re.sub(r"(?:\+?\d[ \t\-()]*){10,}", "", cleaned)
    # Разделители-«рамки» (горизонтальные линии из ─━═│┃▬)
    cleaned = re.sub(r"[─━═│┃▬─]{3,}", "", cleaned)
    # Лишние символы форматирования
    cleaned = re.sub(r"\*+|#+|—", "", cleaned)
    # Балансировка скобок
    while cleaned.count(")") > cleaned.count("("):
        cleaned = cleaned.replace(")", "", 1)
    while cleaned.count("(") > cleaned.count(")"):
        cleaned = cleaned.replace("(", "", 1)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"[,;:.]{2,}", ",", cleaned)
    cleaned = re.sub(r"[\s⠀]+", " ", cleaned).strip(" ,.;:-")
    while True:
        new = re.sub(r"\s+\S{1,2}$", "", cleaned).strip(" ,.;:-")
        if new == cleaned:
            break
        cleaned = new
    return cleaned[:400] or "Скам в P2P (Bybit/MEXC)"


async def _store(
    dsn: str,
    extracted: Extracted,
    source_chat_id: int,
    source_msg_id: int,
    source_url: str,
    raw_text: str,
    resolved: dict[str, int | None],
    category: str = "general",
) -> int:
    # Вырезаем из reason всё, что уже в реквизитах: nicks + ФИО + банки
    _strip_tokens = (
        list(extracted.bybit_nicknames)
        + list(extracted.full_names)
        + list(extracted.banks)
    )
    summary = _summary_from_post(raw_text, also_strip=_strip_tokens)
    written = 0
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute("SET search_path TO skibidi, public")
        async with conn.cursor() as cur:
            for uname in extracted.usernames:
                tg_id = resolved.get(uname.lower())
                if tg_id is None:
                    # Резолв не удался (флуд или временная ошибка) — кладём в pending,
                    # фоновая корутина dorezolv_loop попытается позже.
                    await cur.execute(
                        """
                        INSERT INTO scam_pending_usernames
                            (username, source_chat_id, source_msg_id, source_url, summary, category)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username, source_msg_id) DO NOTHING
                        """,
                        (uname, source_chat_id, source_msg_id, source_url, summary, category),
                    )
                    continue
                await cur.execute(
                    """
                    INSERT INTO scam_blacklist (target_tg_id, target_username, reason, source_chat_id, source_msg_id, category)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (target_tg_id) DO UPDATE
                    SET target_username = EXCLUDED.target_username,
                        source_chat_id  = COALESCE(scam_blacklist.source_chat_id, EXCLUDED.source_chat_id),
                        source_msg_id   = COALESCE(scam_blacklist.source_msg_id,  EXCLUDED.source_msg_id),
                        category        = CASE WHEN scam_blacklist.category = 'general' THEN EXCLUDED.category ELSE scam_blacklist.category END,
                        votes = scam_blacklist.votes + 1
                    """,
                    (tg_id, uname, summary, source_chat_id, source_msg_id, category),
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
                ("account_short", extracted.account_shorts),
                ("bybit_nickname", extracted.bybit_nicknames),
                ("bybit_uid_numeric", extracted.bybit_uids_numeric),
                ("full_name", extracted.full_names),
                ("bank", extracted.banks),
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


async def _process_message(client: TelegramClient, dsn: str, msg, category: str = "general") -> None:
    text = msg.text or msg.message or ""
    if not text:
        return
    ext = extract(text)
    if not (
        ext.usernames or ext.phones or ext.cards or ext.bybit_uids or ext.mexc_uids
        or ext.account_shorts or ext.bybit_nicknames or ext.bybit_uids_numeric
        or ext.full_names or ext.banks
    ):
        return

    resolved: dict[str, int | None] = {}
    for u in ext.usernames:
        resolved[u.lower()] = await _resolve(client, u)
        # sleep между резолвами только если реально дёргали API (не из кэша)
        if _time.monotonic() < _GLOBAL_FLOOD_UNTIL:
            continue
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
        category=category,
    )
    LOG.info(
        "scam_ingest: чан=%s msg=%s usernames=%d phones=%d cards=%d uid=%d → %d записей",
        handle, msg.id, len(ext.usernames), len(ext.phones), len(ext.cards),
        len(ext.bybit_uids) + len(ext.mexc_uids), n,
    )


def channels_from_env() -> list[tuple[str | int, str]]:
    """Парсит SKIBIDI_SCAM_INGEST_CHANNELS в список (channel, category).
    Формат: `name_or_id:category` (через запятую). Если категория не указана — 'general'.
    Если name выглядит как численный ID (опц. минус + цифры) — кастуется в int,
    тогда Telethon обойдёт ResolveUsernameRequest и FLOOD WAIT.
    """
    raw = os.environ.get("SKIBIDI_SCAM_INGEST_CHANNELS", "").strip()
    out: list[tuple[str | int, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            ch, cat = item.split(":", 1)
            ch = ch.strip().lstrip("@")
            cat = cat.strip() or "general"
        else:
            ch = item.lstrip("@")
            cat = "general"
        try:
            ch_val: str | int = int(ch)
        except ValueError:
            ch_val = ch
        out.append((ch_val, cat))
    return out


async def dorezolv_pending_usernames(
    client: TelegramClient, dsn: str, *, batch_size: int = 50, sleep_between_s: float = 8.0,
) -> int:
    """Один проход: достаёт batch юзернеймов из scam_pending_usernames, резолвит,
    при успехе создаёт записи в scam_blacklist + UPDATE scam_credentials.scammer_tg_id
    для всех записей этого поста + удаляет из pending. Возвращает кол-во разрешённых."""
    resolved_n = 0
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute("SET search_path TO skibidi, public")
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT DISTINCT ON (lower(username)) lower(username) AS uname
                FROM scam_pending_usernames
                ORDER BY lower(username), last_try_at NULLS FIRST, attempt_count ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            unames = [r[0] for r in await cur.fetchall()]

        if not unames:
            return 0
        LOG.info("dorezolv: batch %d юзернеймов на резолв", len(unames))

        for uname in unames:
            if _time.monotonic() < _GLOBAL_FLOOD_UNTIL:
                LOG.info("dorezolv: глобальный флуд активен, прерываем batch")
                break
            tg_id = await _resolve(client, uname)
            async with conn.cursor() as cur:
                if tg_id is None:
                    await cur.execute(
                        """
                        UPDATE scam_pending_usernames
                        SET attempt_count = attempt_count + 1, last_try_at = now()
                        WHERE lower(username) = %s
                        """,
                        (uname,),
                    )
                    await conn.commit()
                    if _time.monotonic() < _GLOBAL_FLOOD_UNTIL:
                        break
                    await asyncio.sleep(sleep_between_s)
                    continue
                # Получили tg_id — обновляем blacklist + credentials, удаляем pending
                await cur.execute(
                    """
                    INSERT INTO scam_blacklist
                        (target_tg_id, target_username, reason, source_chat_id, source_msg_id, category)
                    SELECT %s, username, summary, source_chat_id, source_msg_id, category
                    FROM scam_pending_usernames
                    WHERE lower(username) = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    ON CONFLICT (target_tg_id) DO UPDATE
                    SET target_username = EXCLUDED.target_username,
                        source_chat_id  = COALESCE(scam_blacklist.source_chat_id, EXCLUDED.source_chat_id),
                        source_msg_id   = COALESCE(scam_blacklist.source_msg_id,  EXCLUDED.source_msg_id),
                        votes = scam_blacklist.votes + 1
                    """,
                    (tg_id, uname),
                )
                await cur.execute(
                    """
                    UPDATE scam_credentials sc
                    SET scammer_tg_id = %s
                    FROM scam_pending_usernames p
                    WHERE p.lower_username = lower(p.username)
                      AND lower(p.username) = %s
                      AND sc.source_chat_id = p.source_chat_id
                      AND sc.source_msg_id  = p.source_msg_id
                      AND sc.scammer_tg_id IS NULL
                    """,
                    (tg_id, uname),
                ) if False else await cur.execute(
                    """
                    UPDATE scam_credentials sc
                    SET scammer_tg_id = %s
                    FROM scam_pending_usernames p
                    WHERE lower(p.username) = %s
                      AND sc.source_chat_id = p.source_chat_id
                      AND sc.source_msg_id  = p.source_msg_id
                      AND sc.scammer_tg_id IS NULL
                    """,
                    (tg_id, uname),
                )
                await cur.execute(
                    "DELETE FROM scam_pending_usernames WHERE lower(username) = %s",
                    (uname,),
                )
                await conn.commit()
                resolved_n += 1
            await asyncio.sleep(sleep_between_s)
    if resolved_n:
        LOG.info("dorezolv: %d юзернеймов успешно резолвнуто", resolved_n)
    return resolved_n


async def seed_pending_from_existing(dsn: str, *, limit: int | None = None) -> int:
    """Одноразовый seed: проходит по scam_credentials с scammer_tg_id IS NULL,
    перепарсит raw_text → extract().usernames и кладёт в pending для последующего
    дорезолва через dorezolv_loop. Возвращает кол-во созданных записей в pending."""
    inserted = 0
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await conn.execute("SET search_path TO skibidi, public")
        sql = """
            SELECT DISTINCT ON (source_chat_id, source_msg_id)
                source_chat_id, source_msg_id, source_url, raw_text
            FROM scam_credentials
            WHERE scammer_tg_id IS NULL
              AND raw_text IS NOT NULL
              AND source_chat_id IS NOT NULL
              AND source_msg_id IS NOT NULL
            ORDER BY source_chat_id, source_msg_id
        """
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT %s"
            params = (limit,)
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        LOG.info("seed: %d уникальных постов с NULL scammer_tg_id", len(rows))
        for (chat_id, msg_id, url, raw) in rows:
            ext = extract(raw or "")
            if not ext.usernames:
                continue
            summary = _summary_from_post(raw or "", also_strip=list(ext.bybit_nicknames) + list(ext.full_names) + list(ext.banks))
            category = "p2p" if chat_id == -1003115834241 else "general"
            async with conn.cursor() as cur:
                for u in ext.usernames:
                    await cur.execute(
                        """
                        INSERT INTO scam_pending_usernames
                            (username, source_chat_id, source_msg_id, source_url, summary, category)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username, source_msg_id) DO NOTHING
                        """,
                        (u, chat_id, msg_id, url, summary, category),
                    )
                    inserted += cur.rowcount or 0
            await conn.commit()
    LOG.info("seed: создано %d новых pending-записей", inserted)
    return inserted


async def run_dorezolv_loop(
    client: TelegramClient, dsn: str, *, interval_s: int = 3600,
) -> None:
    """Фоновый таск: раз в N сек запускает один проход dorezolv_pending_usernames."""
    LOG.info("dorezolv_loop: запущен, interval=%ss", interval_s)
    while True:
        try:
            await dorezolv_pending_usernames(client, dsn)
        except Exception:  # noqa: BLE001
            LOG.exception("dorezolv: ошибка в итерации")
        await asyncio.sleep(interval_s)


async def register_listeners(
    client: TelegramClient,
    channels: Iterable[tuple[str, str]],
    dsn: str,
    *,
    backfill_on_start: int = 0,
) -> None:
    chan_list = list(channels)
    for ch, cat in chan_list:
        try:
            entity = await client.get_entity(ch)
        except Exception as e:  # noqa: BLE001
            LOG.warning("scam_ingest: не смог открыть @%s: %s", ch, e)
            continue

        async def _handler(event, _dsn=dsn, _cat=cat):
            try:
                await _process_message(client, _dsn, event.message, category=_cat)
            except Exception:  # noqa: BLE001
                LOG.exception("scam_ingest: ошибка обработки сообщения")

        client.add_event_handler(_handler, events.NewMessage(chats=entity))
        LOG.info("scam_ingest: подписан на @%s (id=%s, category=%s)", ch, entity.id, cat)

    if backfill_on_start > 0 and chan_list:
        async def _run_backfill():
            try:
                await backfill(client, chan_list, dsn, limit=backfill_on_start)
            except Exception:  # noqa: BLE001
                LOG.exception("scam_ingest: автобэкфилл провалился")
        asyncio.create_task(_run_backfill())
        LOG.info("scam_ingest: автобэкфилл запущен в фоне (limit=%d)", backfill_on_start)


async def backfill(client: TelegramClient, channels: Iterable[tuple[str, str]], dsn: str, limit: int = 200) -> None:
    """Однократный backfill последних `limit` постов из каждого канала с категорией."""
    for ch, cat in channels:
        try:
            entity = await client.get_entity(ch)
        except Exception as e:  # noqa: BLE001
            LOG.warning("scam_ingest backfill: @%s — %s", ch, e)
            continue
        LOG.info("scam_ingest backfill: @%s, последние %d постов (category=%s)", ch, limit, cat)
        n = 0
        async for msg in client.iter_messages(entity, limit=limit):
            try:
                await _process_message(client, dsn, msg, category=cat)
                n += 1
                if n % 50 == 0:
                    LOG.info("scam_ingest backfill: @%s — прогресс %d/%d", ch, n, limit)
            except FloodWaitError as e:
                LOG.warning("scam_ingest backfill: FloodWait %ss, жду", e.seconds)
                await asyncio.sleep(min(e.seconds, 300))
            except Exception:  # noqa: BLE001
                LOG.exception("backfill error")
        LOG.info("scam_ingest backfill: @%s — обработано %d сообщений", ch, n)
