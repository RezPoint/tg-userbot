from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _first_env(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v and v.strip():
            return v.strip()
    return ""


@dataclass(frozen=True)
class RuntimeConfig:
    api_id: int
    api_hash: str
    bot_token: str
    user_session_path: Path
    bot_session_path: Path
    log_level: str
    supabase_dsn: str

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        load_dotenv()
        api_id_raw = _first_env("TELEGRAM_API_ID", "API_ID")
        api_hash = _first_env("TELEGRAM_API_HASH", "API_HASH")
        bot_token = _first_env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
        missing = [
            n for n, v in {
                "TELEGRAM_API_ID": api_id_raw,
                "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_BOT_TOKEN": bot_token,
            }.items() if not v
        ]
        if missing:
            raise RuntimeError(f"Missing env: {', '.join(missing)}")
        try:
            api_id = int(api_id_raw)
        except ValueError as e:
            raise RuntimeError("TELEGRAM_API_ID must be int") from e
        return cls(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            user_session_path=Path(os.getenv("USER_SESSION_PATH", "sessions/userbot_user")),
            bot_session_path=Path(os.getenv("BOT_SESSION_PATH", "sessions/userbot_delivery")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            supabase_dsn=_first_env("SUPABASE_DSN", "DB_DSN", "DATABASE_URL"),
        )
