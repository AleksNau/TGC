from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    id: str
    symbol: str
    side: str
    price: float | None
    amount: float | None
    cost: float | None
    status: str | None


class ExchangeError(Exception):
    pass


class InsufficientFundsError(ExchangeError):
    pass


class MarketNotFoundError(ExchangeError):
    pass


class BybitClient:
    def __init__(
        self,
        api_key: Optional[str],
        api_secret: Optional[str],
        dry_run: bool = True,
    ) -> None:
        self.dry_run = dry_run
        self.exchange = ccxt.bybit({
            "apiKey": api_key or "",
            "secret": api_secret or "",
            "options": {"defaultType": "spot"},
            "enableRateLimit": True,
        })

    async def load_markets(self) -> None:
        await self.exchange.load_markets()

    def normalize_symbol(self, ticker: str) -> str:
        base = ticker.strip().upper()
        return f"{base}/USDT"

    def has_market(self, symbol: str) -> bool:
        return symbol in self.exchange.markets

    async def ensure_market(self, symbol: str) -> None:
        if not self.has_market(symbol):
            raise MarketNotFoundError(f"Market not found: {symbol}")

    async def get_ticker_price(self, symbol: str) -> float:
        ticker = await self.exchange.fetch_ticker(symbol)
        return float(ticker.get("last") or ticker.get("close") or 0.0)

    async def get_usdt_balance(self) -> float:
        balance = await self.exchange.fetch_balance()
        usdt = balance.get("free", {}).get("USDT")
        if usdt is None:
            # Some exchanges return structure under total/free
            usdt = (balance.get("total", {}) or {}).get("USDT", 0) - (
                (balance.get("used", {}) or {}).get("USDT", 0)
            )
        return float(usdt or 0.0)

    async def market_buy_for_cost(self, symbol: str, usdt_cost: float) -> OrderResult:
        await self.ensure_market(symbol)

        if self.dry_run:
            logger.info("DRY_RUN is enabled; not placing real order")
            price = await self.get_ticker_price(symbol)
            amount = usdt_cost / price if price > 0 else None
            return OrderResult(
                id="dry-run",
                symbol=symbol,
                side="buy",
                price=price,
                amount=amount,
                cost=usdt_cost,
                status="simulated",
            )

        balance = await self.get_usdt_balance()
        if balance + 1e-8 < usdt_cost:
            raise InsufficientFundsError(
                f"Insufficient USDT balance: have {balance:.4f}, need {usdt_cost:.4f}"
            )

        price = await self.get_ticker_price(symbol)
        min_cost = self._min_cost_for_symbol(symbol)
        if min_cost and usdt_cost < min_cost:
            logger.info(
                "Adjusting cost to exchange minimum: requested=%s, min=%s",
                usdt_cost,
                min_cost,
            )
            usdt_cost = min_cost

        amount = usdt_cost / price if price > 0 else None
        if not amount or amount <= 0:
            raise ExchangeError("Calculated amount is invalid")

        # Place market order in quote currency by sizing base amount.
        order = await self.exchange.create_order(symbol, "market", "buy", amount)
        return OrderResult(
            id=str(order.get("id")),
            symbol=order.get("symbol", symbol),
            side=order.get("side", "buy"),
            price=float(order.get("average") or order.get("price") or price or 0.0),
            amount=float(order.get("amount") or amount or 0.0),
            cost=float(order.get("cost") or usdt_cost or 0.0),
            status=str(order.get("status") or "unknown"),
        )

    def _min_cost_for_symbol(self, symbol: str) -> float | None:
        market = self.exchange.markets.get(symbol)
        if not market:
            return None
        # Bybit spot often exposes limits with cost or amount and price precision
        limits = market.get("limits") or {}
        cost_limit = limits.get("cost") or {}
        min_cost = cost_limit.get("min")
        return float(min_cost) if min_cost else None
