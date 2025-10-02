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
from .settings import Settings

logger = logging.getLogger(__name__)


BUY_PREFIX = "BUY_"
CANCEL = "CANCEL"


def _build_buy_keyboard(amounts: list[int], currency: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"Купить {amt} {currency}", callback_data=f"{BUY_PREFIX}{amt}")]
        for amt in amounts
    ]
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data=CANCEL)])
    return InlineKeyboardMarkup(buttons)


class TradingBot:
    def __init__(self, exchange: BybitClient, settings: Settings) -> None:
        self.exchange = exchange
        self.settings = settings

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
        base_quote = self.settings.base_currency
        symbol_usdc = self.exchange.symbol_with_quote(ticker, "USDC")
        symbol_usdt = self.exchange.symbol_with_quote(ticker, "USDT")

        # Ensure markets are loaded to check availability
        try:
            await self.exchange.load_markets()
            await self.exchange.ensure_market(symbol_usdc)
            chosen_symbol = symbol_usdc
            chosen_quote = "USDC"
        except MarketNotFoundError:
            chosen_symbol = symbol_usdt
            chosen_quote = "USDT"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load markets: %s", exc)
            await update.effective_chat.send_message("Ошибка при загрузке рынков")
            return

        # Fetch coin info and price
        coin = await search_coin_by_ticker(ticker)
        try:
            price = await self.exchange.get_ticker_price(chosen_symbol)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch price: %s", exc)
            await update.effective_chat.send_message("Ошибка при получении цены")
            return

        title = (
            f"Найдена пара {chosen_symbol}.\n"
            f"Монета: {(coin.name if coin else ticker.upper())}\n"
            f"Цена: {price:.6f} {chosen_quote}"
        )

        keyboard = _build_buy_keyboard([10, 20], self.settings.base_currency)

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
        context.chat_data["symbol_usdc"] = symbol_usdc
        context.chat_data["symbol_usdt"] = symbol_usdt
        context.chat_data["chosen_symbol"] = chosen_symbol
        context.chat_data["chosen_quote"] = chosen_quote

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
            base_amount = float(amount_str)
        except ValueError:
            await query.message.reply_text("Некорректная сумма")
            return

        symbol_usdc = context.chat_data.get("symbol_usdc")
        symbol_usdt = context.chat_data.get("symbol_usdt")
        chosen_symbol = context.chat_data.get("chosen_symbol")
        chosen_quote = context.chat_data.get("chosen_quote")
        if not symbol_usdc or not symbol_usdt or not chosen_symbol or not chosen_quote:
            await query.message.reply_text("Не найден контекст для покупки. Повторите /buy")
            return

        # Determine execution flow:
        log_steps: list[str] = []
        try:
            if chosen_quote == "USDC":
                # Verify USDC balance
                usdc_balance = await self.exchange.get_usdc_balance()
                if usdc_balance + 1e-8 < base_amount:
                    usdt_balance = await self.exchange.get_usdt_balance()
                    msg = (
                        f"Недостаточно средств (баланс USDC/USDT: {usdc_balance:.2f}/{usdt_balance:.2f})"
                    )
                    logger.info(msg)
                    await query.message.reply_text(msg)
                    return
                # Buy directly with USDC
                result = await self.exchange.market_buy_for_cost(symbol_usdc, base_amount)
                spent_text = f"Списано {result.cost:.2f} USDC"
            else:
                # chosen_quote == "USDT"; compute target USDT cost equivalent to base_amount USDC
                conv_price = await self.exchange.get_ticker_price("USDC/USDT")
                target_usdt_cost = base_amount * conv_price
                usdt_balance = await self.exchange.get_usdt_balance()
                usdc_balance = await self.exchange.get_usdc_balance()

                total_usdt_equiv = usdt_balance + usdc_balance * conv_price
                if total_usdt_equiv + 1e-8 < target_usdt_cost:
                    msg = (
                        f"Недостаточно средств (баланс USDC/USDT: {usdc_balance:.2f}/{usdt_balance:.2f})"
                    )
                    logger.info(msg)
                    await query.message.reply_text(msg)
                    return

                if usdt_balance + 1e-8 < target_usdt_cost:
                    need_usdt = target_usdt_cost - usdt_balance
                    # Estimate required USDC with 0.3% buffer
                    usdc_needed = need_usdt / max(conv_price, 1e-12) * 1.003
                    usdc_needed = min(usdc_needed, usdc_balance)
                    conv_order, received_usdt = await self.exchange.convert_usdc_to_usdt(usdc_needed)
                    step = (
                        f"Обменял {conv_order.amount:.2f} USDC на {received_usdt:.2f} USDT"
                        if not self.exchange.dry_run
                        else f"[DRY_RUN] Обменял бы {usdc_needed:.2f} USDC на ~{received_usdt:.2f} USDT"
                    )
                    log_steps.append(step)
                    logger.info(step)
                # Now buy with USDT for the USDC-equivalent target cost
                result = await self.exchange.market_buy_for_cost(symbol_usdt, target_usdt_cost)
                spent_text = f"Списано {result.cost:.2f} USDT"
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

        steps_text = "\n".join(log_steps)
        msg = (
            f"Режим: {'DRY_RUN' if self.exchange.dry_run else 'LIVE'}\n"
            + (steps_text + "\n" if steps_text else "")
            + f"Куплено {result.amount if result.amount is not None else '—'} {result.symbol.split('/')[0]} по цене {result.price if result.price is not None else '—'} {result.symbol.split('/')[1]}\n"
            + spent_text + "\n"
            + f"Статус: {result.status}"
        )
        await query.message.reply_text(msg)
