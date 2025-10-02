from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

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

    def symbol_with_quote(self, ticker: str, quote: str) -> str:
        base = ticker.strip().upper()
        return f"{base}/{quote.strip().upper()}"

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

    async def get_usdc_balance(self) -> float:
        balance = await self.exchange.fetch_balance()
        usdc = balance.get("free", {}).get("USDC")
        if usdc is None:
            usdc = (balance.get("total", {}) or {}).get("USDC", 0) - (
                (balance.get("used", {}) or {}).get("USDC", 0)
            )
        return float(usdc or 0.0)

    async def market_buy_for_cost(self, symbol: str, quote_cost: float) -> OrderResult:
        await self.ensure_market(symbol)

        if self.dry_run:
            logger.info("DRY_RUN is enabled; not placing real order")
            price = await self.get_ticker_price(symbol)
            amount = quote_cost / price if price > 0 else None
            return OrderResult(
                id="dry-run",
                symbol=symbol,
                side="buy",
                price=price,
                amount=amount,
                cost=quote_cost,
                status="simulated",
            )

        # Validate we have enough of the quote currency; caller should ensure
        # conversion if needed.
        quote = symbol.split("/")[-1]
        if quote == "USDT":
            balance = await self.get_usdt_balance()
        elif quote == "USDC":
            balance = await self.get_usdc_balance()
        else:
            balance = 0.0

        if balance + 1e-8 < quote_cost:
            raise InsufficientFundsError(
                f"Insufficient {quote} balance: have {balance:.4f}, need {quote_cost:.4f}"
            )

        price = await self.get_ticker_price(symbol)
        min_cost = self._min_cost_for_symbol(symbol)
        if min_cost and quote_cost < min_cost:
            logger.info(
                "Adjusting cost to exchange minimum: requested=%s, min=%s",
                quote_cost,
                min_cost,
            )
            quote_cost = min_cost

        amount = quote_cost / price if price > 0 else None
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
            cost=float(order.get("cost") or quote_cost or 0.0),
            status=str(order.get("status") or "unknown"),
        )

    async def convert_usdc_to_usdt(self, usdc_amount: float) -> Tuple[OrderResult, float]:
        """Convert USDC to USDT via market sell on USDC/USDT.

        Returns tuple of (order_result, received_usdt_estimate).
        In DRY_RUN, simulates using ticker price.
        """
        symbol = "USDC/USDT"
        await self.ensure_market(symbol)

        if usdc_amount <= 0:
            raise ExchangeError("Conversion amount must be > 0")

        if self.dry_run:
            price = await self.get_ticker_price(symbol)  # price in USDT per USDC
            received = usdc_amount * price
            logger.info("DRY_RUN: would convert %.4f USDC -> %.4f USDT at ~%.6f", usdc_amount, received, price)
            return (
                OrderResult(
                    id="dry-run",
                    symbol=symbol,
                    side="sell",
                    price=price,
                    amount=usdc_amount,
                    cost=received,
                    status="simulated",
                ),
                received,
            )

        # Ensure enough USDC
        usdc_balance = await self.get_usdc_balance()
        if usdc_balance + 1e-8 < usdc_amount:
            raise InsufficientFundsError(
                f"Insufficient USDC balance: have {usdc_balance:.4f}, need {usdc_amount:.4f}"
            )

        price = await self.get_ticker_price(symbol)
        min_cost = self._min_cost_for_symbol(symbol)
        # For sell USDC/USDT, cost limit is in quote (USDT); we approximate with amount*price
        if min_cost and usdc_amount * price < min_cost:
            adj = min_cost / max(price, 1e-12)
            logger.info("Adjusting USDC sell to min cost: requested=%s, min_amount=%s", usdc_amount, adj)
            usdc_amount = adj

        order = await self.exchange.create_order(symbol, "market", "sell", usdc_amount)
        received = float(order.get("cost") or (usdc_amount * price))
        return (
            OrderResult(
                id=str(order.get("id")),
                symbol=order.get("symbol", symbol),
                side=order.get("side", "sell"),
                price=float(order.get("average") or order.get("price") or price or 0.0),
                amount=float(order.get("amount") or usdc_amount or 0.0),
                cost=received,
                status=str(order.get("status") or "unknown"),
            ),
            received,
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
