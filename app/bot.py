from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .coingecko import search_coin_by_ticker
from .exchange import (
    BybitClient,
    ExchangeError,
    InsufficientFundsError,
    MarketNotFoundError,
)

logger = logging.getLogger(__name__)


BUY_PREFIX = "BUY_USDT_"
CANCEL = "CANCEL"


def _build_buy_keyboard(amounts: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"Купить {amt} USDT", callback_data=f"{BUY_PREFIX}{amt}")]
        for amt in amounts
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data=CANCEL)])
    return InlineKeyboardMarkup(buttons)


class TradingBot:
    def __init__(self, exchange: BybitClient) -> None:
        self.exchange = exchange

    def build_app(self, token: str) -> Application:
        app = (
            ApplicationBuilder()
            .token(token)
            .concurrent_updates(True)
            .rate_limiter(AIORateLimiter())
            .build()
        )
        app.add_handler(CommandHandler("buy", self.cmd_buy))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        return app

    async def cmd_buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        args = context.args or []
        if not args:
            await update.effective_chat.send_message(
                "Использование: /buy <ticker> — например /buy pepe"
            )
            return
        ticker = args[0]
        symbol = self.exchange.normalize_symbol(ticker)

        # Ensure markets are loaded to check availability
        try:
            await self.exchange.load_markets()
            await self.exchange.ensure_market(symbol)
        except MarketNotFoundError:
            await update.effective_chat.send_message(
                f"Пара не найдена на Bybit (спот): {symbol}"
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load markets: %s", exc)
            await update.effective_chat.send_message("Ошибка при загрузке рынков")
            return

        # Fetch coin info and price
        coin = await search_coin_by_ticker(ticker)
        try:
            price = await self.exchange.get_ticker_price(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch price: %s", exc)
            await update.effective_chat.send_message("Ошибка при получении цены")
            return

        title = (
            f"Найдена пара {symbol}.\n"
            f"Монета: {(coin.name if coin else ticker.upper())}\n"
            f"Цена: {price:.6f} USDT"
        )

        keyboard = _build_buy_keyboard([10, 20])

        if coin and coin.thumb:
            try:
                await update.effective_chat.send_photo(
                    photo=coin.thumb,
                    caption=title,
                    reply_markup=keyboard,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sending photo failed, fallback to text: %s", exc)

        await update.effective_chat.send_message(text=title, reply_markup=keyboard)

        # Store context for callbacks (per-chat)
        context.chat_data["symbol"] = symbol

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""
        if data == CANCEL:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Отменено")
            return

        if not data.startswith(BUY_PREFIX):
            return

        amount_str = data[len(BUY_PREFIX) :]
        try:
            usdt_amount = float(amount_str)
        except ValueError:
            await query.message.reply_text("Некорректная сумма")
            return

        symbol = context.chat_data.get("symbol")
        if not symbol:
            await query.message.reply_text("Не найден контекст для покупки. Повторите /buy")
            return

        try:
            result = await self.exchange.market_buy_for_cost(symbol, usdt_amount)
        except InsufficientFundsError as exc:
            await query.message.reply_text(str(exc))
            return
        except MarketNotFoundError:
            await query.message.reply_text("Пара больше недоступна")
            return
        except ExchangeError as exc:
            logger.exception("Exchange error: %s", exc)
            await query.message.reply_text("Ошибка биржи: не удалось выполнить ордер")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error: %s", exc)
            await query.message.reply_text("Неизвестная ошибка")
            return

        msg = (
            f"Ордер BUY выполнен (режим: {'DRY_RUN' if self.exchange.dry_run else 'LIVE'}).\n"
            f"Пара: {result.symbol}\n"
            f"Цена: {result.price if result.price is not None else '—'}\n"
            f"Кол-во: {result.amount if result.amount is not None else '—'}\n"
            f"Стоимость: {result.cost if result.cost is not None else '—'} USDT\n"
            f"Статус: {result.status}"
        )
        await query.message.reply_text(msg)
