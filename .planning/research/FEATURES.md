# Feature Research

## Table Stakes

- Source list for Telegram channels/groups.
- Include keywords and stop words.
- Dedupe by source and message id.
- Telegram delivery with source, link, excerpt and matched terms.
- Startup catch-up so fresh posts are not missed after restarts.
- Clear setup docs for Telegram API credentials and bot token.

## Differentiators

- Weighted keyword score instead of simple contains-any.
- `/test` command to validate a sample post against the filter.
- `/status`, `/sources`, `/keywords` commands for quick operational visibility.
- Contacts extraction from post text.

## Anti-Features

- Auto-replies to clients.
- Scraping private groups without account access.
- Silent token/session storage in git.
- Hard-coded credentials.

