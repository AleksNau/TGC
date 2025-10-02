from __future__ import annotations

import asyncio
import logging

from app.bot import TradingBot
from app.exchange import BybitClient
from app.settings import load_settings, setup_logging


async def main_async() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_file)
    logger = logging.getLogger(__name__)

    logger.info("Starting bot (dry_run=%s)", settings.dry_run)

    exchange = BybitClient(
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        dry_run=settings.dry_run,
    )

    bot = TradingBot(exchange, settings)
    app = bot.build_app(settings.telegram_token)

    await app.initialize()
    await app.start()
    try:
        await app.updater.start_polling(drop_pending_updates=True)
        await asyncio.Event().wait()  # run forever
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main_async())
