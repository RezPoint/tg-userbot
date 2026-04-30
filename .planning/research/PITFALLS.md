# Pitfalls Research

## Bot API Confusion

Warning sign: app only has bot token and no `api_id/api_hash`.

Prevention: use a user Telethon session for reading, bot token only for delivery.

## Source Noise

Warning sign: too many SMM/design/low-quality "без опыта" posts.

Prevention: hard stop-words plus weighted score. Review first 50-100 notifications.

## Duplicate Leads

Warning sign: catch-up resends old posts on each restart.

Prevention: SQLite unique key on `(source, message_id)`.

## Session Leakage

Warning sign: `.env` or `.session` files in git status.

Prevention: `.gitignore` covers secrets, sessions and local database.

## Private Group Access

Warning sign: Telethon cannot resolve or read a source.

Prevention: join private groups manually with the user account or remove them from sources.

