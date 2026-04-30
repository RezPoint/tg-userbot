# Telegram Freelance Lead Bot

## What This Is

Личный Telegram-бот для фрилансера-разработчика, который ищет свежие заказы на Telegram-ботов, парсинг, автоматизацию, API-интеграции и Python/backend-разработку. Система мониторит выбранные Telegram-каналы/чаты/группы, фильтрует сообщения по ключевым и стоп-словам и присылает релевантные лиды в отдельного Telegram-бота.

## Core Value

Быстро находить релевантные заказы на разработку раньше, чем они утонут в шуме фриланс-каналов.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Мониторить 10-20 стартовых Telegram-источников по фрилансу и разработке.
- [ ] Фильтровать сообщения по редактируемым в коде ключевым словам и стоп-словам.
- [ ] Отправлять найденные лиды в Telegram-бота с ссылкой на пост, источником, совпавшими словами и контактами.
- [ ] Дедуплицировать сообщения, чтобы один и тот же пост не приходил повторно.
- [ ] Дать простой запуск через `.env`, requirements и README.

### Out of Scope

- Автоматический отклик заказчикам — риск спама и блокировок; только ручной отклик.
- Обход приватности или парсинг закрытых чатов без доступа — мониторить только источники, к которым есть доступ аккаунта.
- Полноценная веб-админка — для v1 достаточно редактировать источники и фильтры в коде.
- ML/NLP-классификатор — сначала нужен простой и контролируемый baseline на ключевых словах.

## Context

Пользователь говорит по-русски и ищет заказы для себя как фрилансер, вероятно в нише Telegram-ботов и разработки. У пользователя есть Telegram API и bot token. Для чтения каналов нужен пользовательский MTProto-клиент (`api_id/api_hash`), потому что Telegram Bot API не читает произвольные каналы, если бот не добавлен туда с нужными правами. Bot token используется для доставки уведомлений.

Стартовые источники подобраны по публичным страницам Telegram/TGStat/Telemetr/Nicegram/TG-каталогам и должны рассматриваться как тестовый набор: качество нужно уточнять по первым уведомлениям.

## Constraints

- **Tech stack**: Python + Telethon + SQLite — минимально, локально, без инфраструктуры.
- **Security**: `.env`, session-файлы и SQLite база не коммитятся — содержат токены и пользовательскую сессию.
- **Telegram access**: приватные группы и чаты работают только если пользовательский аккаунт уже имеет доступ.
- **Noise control**: фильтр должен быть легко редактируемым в коде, потому что релевантность быстро меняется.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Использовать Telethon user client для чтения источников | Bot API не подходит для произвольных каналов/групп | — Pending |
| Использовать Telethon bot client для доставки | Один стек и минимум зависимостей | — Pending |
| Хранить ключевые и стоп-слова в `freelancer_bot/filters.py` | Пользователь прямо попросил указывать их в коде | — Pending |
| Хранить стартовые источники в `freelancer_bot/sources.py` | Проще быстро править тестовый набор 10-20 каналов | — Pending |
| SQLite для дедупликации | Локально, без внешней базы, достаточно для v1 | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `$gsd-transition`):
1. Requirements invalidated? -> Move to Out of Scope with reason
2. Requirements validated? -> Move to Validated with phase reference
3. New requirements emerged? -> Add to Active
4. Decisions to log? -> Add to Key Decisions
5. "What This Is" still accurate? -> Update if drifted

**After each milestone** (via `$gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-30 after initialization*

