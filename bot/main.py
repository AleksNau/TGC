from __future__ import annotations

import asyncio
import os
from typing import Optional

from loguru import logger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from app.config import get_settings
from app.logger import setup_logger
from app.exchange import ExchangeService


settings = get_settings()
setup_logger(settings.log_file)


def format_money(value: float, currency: str) -> str:
    if currency.upper() in {"USDC", "USDT", "USD"}:
        return f"{value:.2f} {currency.upper()}"
    return f"{value:.8f} {currency.upper()}"


def parse_buy_args(text: str) -> Optional[str]:
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    return parts[1].upper()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Бот готов. Используйте /buy <TICKER>")


async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    ticker = parse_buy_args(update.message.text or "")
    if not ticker:
        await update.message.reply_text("Формат: /buy <TICKER>")
        return

    # Offer USDC-sized buttons
    keyboard = [
        [
            InlineKeyboardButton("Купить 10 USDC", callback_data=f"buy:{ticker}:10"),
            InlineKeyboardButton("Купить 20 USDC", callback_data=f"buy:{ticker}:20"),
        ],
        [
            InlineKeyboardButton("Купить 50 USDC", callback_data=f"buy:{ticker}:50"),
            InlineKeyboardButton("Купить 100 USDC", callback_data=f"buy:{ticker}:100"),
        ],
    ]
    await update.message.reply_text(
        f"Покупка {ticker}. Выберите сумму в USDC:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def on_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    _, ticker, amount_str = (query.data or "").split(":")
    desired_usdc = float(amount_str)

    exchange = ExchangeService(
        api_key=settings.bybit_api_key,
        secret=settings.bybit_secret,
        dry_run=settings.dry_run,
    )

    # load markets (ccxt sync)
    await asyncio.get_event_loop().run_in_executor(None, exchange.client.load_markets)

    # STEP 1: Try TICKER/USDC
    steps: list[str] = []
    market_usdc = exchange.check_pair_exists(ticker, "USDC")
    market_usdt = None

    balances = exchange.get_balance()
    usdc_balance = balances.get("USDC", 0.0)
    usdt_balance = balances.get("USDT", 0.0)

    used_quote = "USDC"
    spent_quote = 0.0
    fill_price = 0.0
    filled_amount = 0.0

    try:
        if market_usdc:
            # Ensure we have desired USDC amount, convert from USDT if needed
            conv_steps, available_usdc = exchange.ensure_usdc_for_purchase(desired_usdc)
            steps.extend(conv_steps)
            if available_usdc < desired_usdc:
                await query.edit_message_text(
                    f"Недостаточно средств (баланс USDC/USDT: {format_money(usdc_balance, 'USDC')} / {format_money(usdt_balance, 'USDT')})"
                )
                return

            order, amount, price = exchange.market_buy_by_quote(market_usdc, desired_usdc)
            used_quote = "USDC"
            spent_quote = desired_usdc
            fill_price = price
            filled_amount = amount
        else:
            # Fallback to USDT pair
            market_usdt = exchange.check_pair_exists(ticker, "USDT")
            if not market_usdt:
                await query.edit_message_text(
                    f"Пара {ticker}/USDC и {ticker}/USDT не найдены на Bybit"
                )
                return

            # Ensure we have USDT by converting USDC if needed
            conv_steps, available_for_usdt = exchange.ensure_usdt_for_purchase(desired_usdc)
            steps.extend(conv_steps)
            if available_for_usdt < desired_usdc:
                await query.edit_message_text(
                    f"Недостаточно средств (баланс USDC/USDT: {format_money(usdc_balance, 'USDC')} / {format_money(usdt_balance, 'USDT')})"
                )
                return

            order, amount, price = exchange.market_buy_by_quote(market_usdt, desired_usdc)
            used_quote = "USDT"
            spent_quote = desired_usdc
            fill_price = price
            filled_amount = amount

        # Compose result text
        result_lines = []
        result_lines.extend(steps)
        if settings.dry_run:
            result_lines.append(
                f"DRY_RUN: Купил бы {filled_amount} {ticker} по цене ~{fill_price} {used_quote}"
            )
            result_lines.append(
                f"DRY_RUN: Списал бы {format_money(spent_quote, used_quote)}"
            )
        else:
            result_lines.append(
                f"Куплено {filled_amount} {ticker} по цене {fill_price} {used_quote}"
            )
            result_lines.append(
                f"Списано {format_money(spent_quote, used_quote)}"
            )

        text = "\n".join(result_lines)
        logger.info(text)
        await query.edit_message_text(text)
    except Exception as e:
        logger.exception("Ошибка в покупке")
        await query.edit_message_text(f"Ошибка: {e}")


async def run() -> None:
    token = settings.telegram_token
    if not token:
        logger.error("TELEGRAM_TOKEN не задан в .env")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CallbackQueryHandler(on_buy_callback, pattern=r"^buy:"))

    logger.info("Бот запущен")
    await app.run_polling()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
