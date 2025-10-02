from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    telegram_token: str
    bybit_api_key: str
    bybit_secret: str
    dry_run: bool
    base_currency: str
    log_file: str


def str_to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return default


def get_settings() -> Settings:
    return Settings(
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        bybit_api_key=os.getenv("BYBIT_API_KEY", ""),
        bybit_secret=os.getenv("BYBIT_SECRET", ""),
        dry_run=str_to_bool(os.getenv("DRY_RUN"), default=False),
        base_currency=os.getenv("BASE_CURRENCY", "USDC").upper(),
        log_file=os.getenv("LOG_FILE", "logs/bot.log"),
    )
