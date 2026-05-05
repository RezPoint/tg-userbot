# Telegram Freelance Lead Bot

Telegram Freelance Lead Bot is a small Python bot that monitors selected Telegram channels for freelance orders and sends matching leads to your own Telegram bot.

It works without AI: the matching logic is a transparent keyword/stop-word score that you can edit in code.

## What It Does

- Reads public Telegram channels and groups through your Telegram user session.
- Filters messages by weighted keywords and stop-words.
- Saves processed leads in SQLite to avoid duplicates.
- Sends matching leads to your Telegram bot.
- Lets you subscribe/unsubscribe a chat with `/start` and `/stop`.
- Includes `/status`, `/sources`, `/keywords`, and `/test` commands.

## How It Works

Telegram has two different APIs involved here:

- **Telegram user API** (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) reads channels and groups your account can access.
- **Telegram Bot API** (`TELEGRAM_BOT_TOKEN`) sends matched leads to you.

A bot token alone cannot read arbitrary Telegram channels. The app needs a user session for monitoring and a bot token for delivery.

## Requirements

- Python 3.10+
- Telegram account
- Telegram API credentials from <https://my.telegram.org>
- Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/telegram-freelance-lead-bot.git
cd telegram-freelance-lead-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env`:

```bash
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=123456:bot_token
```

Run:

```bash
python -m freelancer_bot
```

On the first run, Telethon may ask for your phone number and Telegram login code. After the app starts, open your Telegram bot and send `/start` to subscribe that chat to lead notifications.

## Configuration

### Sources

Edit [freelancer_bot/sources.py](freelancer_bot/sources.py) to change monitored Telegram channels.

Each source looks like this:

```python
Source("@freelansim_ru", "Хабр Фриланс", "freelance and development orders")
```

If a source stops resolving, remove it or set `enabled=False`.

### Keywords And Stop-Words

Edit [freelancer_bot/filters.py](freelancer_bot/filters.py).

Important settings:

- `KEYWORDS`: words and phrases that increase the lead score.
- `STOP_WORDS`: words and phrases that reject a message immediately.
- `MIN_SCORE`: minimum score needed to send the lead.

Example:

```python
KEYWORDS = {
    "telegram bot": 5,
    "тг бот": 5,
    "парсер": 4,
    "python": 2,
}

STOP_WORDS = [
    "smm",
    "казино",
    "ставки",
]
```

You can test the filter without connecting to Telegram:

```bash
python -m freelancer_bot --check-filter "Нужно разработать телеграм бот на Python"
```

## Environment Variables

| Variable | Required | Description |
|---|---:|---|
| `TELEGRAM_API_ID` | yes | API ID from <https://my.telegram.org> |
| `TELEGRAM_API_HASH` | yes | API hash from <https://my.telegram.org> |
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_TARGET_CHAT_ID` | no | Chat ID to subscribe automatically. If omitted, send `/start` to the bot. |
| `DATABASE_PATH` | no | SQLite database path. Default: `data/leads.sqlite3` |
| `USER_SESSION_PATH` | no | Telethon user session path. Default: `sessions/freelancer_user` |
| `BOT_SESSION_PATH` | no | Telethon bot session path. Default: `sessions/freelancer_delivery_bot` |
| `CATCH_UP_LIMIT` | no | How many recent messages to scan per source on startup. Default: `25` |
| `SEND_CATCH_UP` | no | Whether to scan recent messages on startup. Default: `true` |
| `LOG_LEVEL` | no | Python log level. Default: `INFO` |

Legacy names `API_ID`, `API_HASH`, `BOT_TOKEN`, and `TARGET_USER_ID` are also supported for compatibility with older parser projects.

## Bot Commands

- `/start` - subscribe current chat to lead notifications.
- `/stop` - unsubscribe current chat.
- `/status` - show source/subscriber/lead counts.
- `/sources` - list enabled sources.
- `/keywords` - preview current keywords and stop-words.
- `/test text` - check whether a sample text passes the filter.

## Running In The Background

Simple local run:

```bash
python -m freelancer_bot
```

Basic background run:

```bash
nohup python -m freelancer_bot > bot.log 2>&1 &
```

For a VPS, use `systemd`, Docker, or a process manager. Keep `.env`, `sessions/`, and `data/` private.

## Tests

```bash
python -m unittest discover -s tests
python -m py_compile freelancer_bot/*.py
```

## Security

Never publish:

- `.env`
- `sessions/`
- `*.session`
- `*.db`
- `data/`

Telegram session files can give access to your Telegram account. Treat them like passwords.

## Responsible Use

Monitor only sources your Telegram account can access. This project is intended for personal lead discovery and manual replies, not spam or automated outreach.

## License

MIT. See [LICENSE](LICENSE).

