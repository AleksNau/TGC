from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3"


@dataclass
class CoinInfo:
    id: str
    symbol: str
    name: str
    thumb: Optional[str]


async def search_coin_by_ticker(ticker: str) -> Optional[CoinInfo]:
    query = ticker.strip().lower()
    if not query:
        return None

    url = f"{COINGECKO_API}/search?query={query}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("CoinGecko search failed: %s", exc)
            return None

    coins = (data or {}).get("coins") or []
    if not coins:
        return None

    # Prefer exact symbol match; fallback to first result
    exact = next((c for c in coins if c.get("symbol", "").lower() == query), None)
    selected = exact or coins[0]

    return CoinInfo(
        id=str(selected.get("id")),
        symbol=str(selected.get("symbol")),
        name=str(selected.get("name")),
        thumb=selected.get("thumb"),
    )
