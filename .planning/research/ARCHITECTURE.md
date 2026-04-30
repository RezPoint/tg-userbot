# Architecture Research

## Components

1. Runtime config loads `.env`.
2. Source config lists Telegram handles.
3. User Telethon client reads source messages.
4. Filter scores text with include keywords and stop words.
5. SQLite stores subscribers and processed leads.
6. Bot Telethon client handles commands and sends matched leads.

## Data Flow

Telegram source message -> user client event -> keyword filter -> SQLite dedupe -> bot delivery -> mark notified.

## Build Order

1. Config, source list and filter.
2. Storage and dedupe.
3. Bot delivery and commands.
4. User client monitoring and catch-up.
5. Docs and smoke tests.

