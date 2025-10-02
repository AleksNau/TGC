from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

import ccxt
from loguru import logger


@dataclass
class MarketInfo:
    symbol: str
    quote: str
    base: str
    price_precision: int
    amount_precision: int
    min_cost: Optional[float]


class ExchangeService:
    def __init__(self, api_key: str, secret: str, dry_run: bool = False) -> None:
        self.client = ccxt.bybit({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.dry_run = dry_run

    async def load_markets(self) -> None:
        await self.client.load_markets()

    def get_balance(self) -> Dict[str, float]:
        bal = self.client.fetch_balance()
        total: Dict[str, float] = {}
        for cur, obj in bal.get("total", {}).items():
            try:
                total[cur.upper()] = float(obj)
            except Exception:
                pass
        # Also include free balances
        for cur, obj in bal.get("free", {}).items():
            try:
                total[cur.upper()] = float(obj)
            except Exception:
                pass
        return total

    def find_market(self, base: str, quote: str) -> Optional[MarketInfo]:
        symbol = f"{base}/{quote}"
        markets = self.client.markets
        if symbol not in markets:
            return None
        m = markets[symbol]
        price_prec = m.get("precision", {}).get("price", 8)
        amount_prec = m.get("precision", {}).get("amount", 8)
        min_cost = None
        if "limits" in m and "cost" in m["limits"] and m["limits"]["cost"].get("min"):
            min_cost = float(m["limits"]["cost"]["min"])  # quote currency
        return MarketInfo(
            symbol=symbol,
            quote=m.get("quote"),
            base=m.get("base"),
            price_precision=price_prec,
            amount_precision=amount_prec,
            min_cost=min_cost,
        )

    def round_to_precision(self, value: float, precision: int) -> float:
        if precision <= 0:
            return float(int(value))
        factor = 10 ** precision
        return math.floor(value * factor) / factor

    def fetch_ticker_price(self, symbol: str) -> float:
        t = self.client.fetch_ticker(symbol)
        # use last price fallback
        price = t.get("last") or t.get("close") or t.get("ask") or t.get("bid")
        return float(price)

    def create_market_order(self, symbol: str, side: str, amount: float) -> Dict:
        logger.info(f"Create market order: {side} {amount} {symbol}")
        if self.dry_run:
            return {
                "status": "dry_run",
                "symbol": symbol,
                "side": side,
                "amount": amount,
            }
        return self.client.create_order(symbol, type="market", side=side, amount=amount)

    def market_buy_by_quote(self, market: MarketInfo, quote_amount: float) -> Tuple[Dict, float, float]:
        price = self.fetch_ticker_price(market.symbol)
        amount = quote_amount / price
        amount = self.round_to_precision(amount, market.amount_precision)
        if market.min_cost and quote_amount < market.min_cost:
            raise ValueError(f"Min cost for {market.symbol} is {market.min_cost}")
        order = self.create_market_order(market.symbol, "buy", amount)
        return order, amount, price

    def convert_currency(self, from_cur: str, to_cur: str, from_amount: float) -> Tuple[List[str], float, float]:
        """Convert between currencies using available spot market.

        Returns (steps, received_to_amount, used_price)
        """
        steps: List[str] = []
        from_cur = from_cur.upper()
        to_cur = to_cur.upper()
        if from_amount <= 0:
            return steps, 0.0, 0.0

        # Prefer direct pair FROM/TO with side=sell
        direct = self.find_market(from_cur, to_cur)
        inverse = self.find_market(to_cur, from_cur) if not direct else None

        if direct:
            price = self.fetch_ticker_price(direct.symbol)
            base_amount = self.round_to_precision(from_amount, direct.amount_precision)
            text = (
                f"Обменял {self.round_to_precision(base_amount, 6)} {from_cur} на ~"
                f"{self.round_to_precision(base_amount * price, 6)} {to_cur} по цене ~"
                f"{self.round_to_precision(price, direct.price_precision)} {to_cur}"
            )
            steps.append(text)
            if not self.dry_run:
                self.create_market_order(direct.symbol, "sell", base_amount)
            else:
                steps.append("DRY_RUN: ордер на конвертацию не исполнен")
            return steps, base_amount * price, price

        if inverse:
            price = self.fetch_ticker_price(inverse.symbol)  # price in FROM per 1 TO
            # We will BUY base=to_cur spending from_amount of quote=from_cur
            base_amount = self.round_to_precision(from_amount / price, inverse.amount_precision)
            text = (
                f"Обменял ~{self.round_to_precision(from_amount, 6)} {from_cur} на "
                f"{self.round_to_precision(base_amount, 6)} {to_cur} по цене ~"
                f"{self.round_to_precision(price, inverse.price_precision)} {from_cur}"
            )
            steps.append(text)
            if not self.dry_run:
                self.create_market_order(inverse.symbol, "buy", base_amount)
            else:
                steps.append("DRY_RUN: ордер на конвертацию не исполнен")
            return steps, base_amount, price

        raise RuntimeError(f"Рынок для конвертации {from_cur}->{to_cur} не найден")

    def ensure_usdt_for_purchase(self, desired_usdc: float) -> Tuple[List[str], float]:
        """Ensure we have enough USDT equivalent to desired_usdc amount.

        Returns (steps, available_in_usdt)
        """
        steps: List[str] = []
        balances = self.get_balance()
        usdc = balances.get("USDC", 0.0)
        usdt = balances.get("USDT", 0.0)

        if usdt >= desired_usdc:
            return steps, desired_usdc

        shortfall = max(0.0, desired_usdc - usdt)
        if shortfall <= 0:
            return steps, desired_usdc

        convert_amount = min(usdc, shortfall)
        if convert_amount <= 0:
            return steps, usdt

        conv_steps, received_usdt, price = self.convert_currency("USDC", "USDT", convert_amount)
        for s in conv_steps:
            logger.info(s)
        steps.extend(conv_steps)
        return steps, min(desired_usdc, usdt + received_usdt)

    def ensure_usdc_for_purchase(self, desired_usdc: float) -> Tuple[List[str], float]:
        """Ensure we have enough USDC to spend desired_usdc on a USDC-quoted pair.

        Returns (steps, available_in_usdc)
        """
        steps: List[str] = []
        balances = self.get_balance()
        usdc = balances.get("USDC", 0.0)
        usdt = balances.get("USDT", 0.0)

        if usdc >= desired_usdc:
            return steps, desired_usdc

        shortfall = max(0.0, desired_usdc - usdc)
        if shortfall <= 0:
            return steps, desired_usdc

        convert_amount = min(usdt, shortfall)
        if convert_amount <= 0:
            return steps, usdc

        conv_steps, received_usdc, price = self.convert_currency("USDT", "USDC", convert_amount)
        for s in conv_steps:
            logger.info(s)
        steps.extend(conv_steps)
        return steps, min(desired_usdc, usdc + received_usdc)

    def check_pair_exists(self, ticker: str, quote: str) -> Optional[MarketInfo]:
        return self.find_market(ticker.upper(), quote.upper())
