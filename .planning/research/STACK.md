# Stack Research

## Recommendation

Use Python with Telethon and SQLite for v1.

## Why

- Telethon can run a user MTProto session for reading public channels, joined groups and chats.
- The same library can run a bot session for delivery via bot token.
- SQLite is enough for dedupe, subscribers and local state.
- A small Python app is easier to run locally or later wrap into systemd/Docker.

## Do Not Use For v1

- Pure Telegram Bot API for parsing: it cannot read arbitrary channels.
- Browser scraping of `t.me/s/...`: brittle, rate-limited and misses private/joined groups.
- Heavy NLP pipeline: not needed before baseline keyword quality is measured.

## Confidence

High for v1. Revisit only if source volume grows enough to need queues, Postgres or a web admin.

