from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    api_id: int
    api_hash: str
    bot_token: str
    target_chat_id: Optional[int]
    database_path: Path
    user_session_path: Path
    bot_session_path: Path
    catch_up_limit: int
    send_catch_up: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        load_dotenv()

        api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        missing = [
            name
            for name, value in {
                "TELEGRAM_API_ID": api_id_raw,
                "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_BOT_TOKEN": bot_token,
            }.items()
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Missing required environment variables: {joined}")

        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_API_ID must be an integer") from exc

        target_chat_id_raw = os.getenv("TELEGRAM_TARGET_CHAT_ID", "").strip()
        target_chat_id = int(target_chat_id_raw) if target_chat_id_raw else None

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            target_chat_id=target_chat_id,
            database_path=Path(os.getenv("DATABASE_PATH", "data/leads.sqlite3")),
            user_session_path=Path(os.getenv("USER_SESSION_PATH", "sessions/freelancer_user")),
            bot_session_path=Path(os.getenv("BOT_SESSION_PATH", "sessions/freelancer_delivery_bot")),
            catch_up_limit=max(0, int(os.getenv("CATCH_UP_LIMIT", "25"))),
            send_catch_up=_bool_env("SEND_CATCH_UP", True),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

