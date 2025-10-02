from __future__ import annotations

import logging
import logging.handlers
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    bybit_api_key: str | None
    bybit_api_secret: str | None
    dry_run: bool
    base_currency: str
    log_level: str
    log_file: str


def _str_to_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    # Load .env if present
    load_dotenv()

    telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
    bybit_api_key = os.getenv("BYBIT_API_KEY")
    bybit_api_secret = os.getenv("BYBIT_API_SECRET")
    dry_run = _str_to_bool(os.getenv("DRY_RUN"), True)
    base_currency = os.getenv("BASE_CURRENCY", "USDC").strip().upper() or "USDC"
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE", "logs/app.log")

    if not telegram_token:
        raise RuntimeError("TELEGRAM_TOKEN is required")

    return Settings(
        telegram_token=telegram_token,
        bybit_api_key=bybit_api_key,
        bybit_api_secret=bybit_api_secret,
        dry_run=dry_run,
        base_currency=base_currency,
        log_level=log_level,
        log_file=log_file,
    )


def setup_logging(log_level: str, log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)

    # File handler (rotating)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Clear existing handlers to avoid duplication on reload
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)
