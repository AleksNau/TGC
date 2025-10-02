# Minimal Telegram Trading Bot (Bybit Spot)

This bot lets you quickly buy a crypto on Bybit Spot using inline buttons for fixed amounts in a base currency (default USDC). It fetches coin info (name + icon) from CoinGecko and places MARKET buy orders via ccxt.

## Features
- `/buy <ticker>` (e.g., `/buy pepe`)
- Confirms pair on Bybit (spot), shows full coin name and current price
- Sends coin icon (from CoinGecko) when available, otherwise a text fallback
- Inline buttons: [Купить 10 USDC] [Купить 20 USDC] [Отмена]
- MARKET buy order via Bybit API using ccxt
- Config from `.env` with DRY_RUN mode (default true)
- Logging to `logs/app.log` and console
- Error messages for missing pair and insufficient balance

## Project structure
```
/app
  main.py
  bot.py
  exchange.py
  coingecko.py
  settings.py
requirements.txt
.env.example
README.md
```

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill your keys
python app/main.py
```

## Configuration
Set the following in `.env`:
- `TELEGRAM_TOKEN`: Telegram bot token
- `BYBIT_API_KEY`, `BYBIT_API_SECRET`: Bybit API keys with TRADE rights only (no withdraw)
- `DRY_RUN`: `true`/`false` (default `true`). When `true`, orders are simulated
- `BASE_CURRENCY`: base quote currency for buttons and preferred pair, default `USDC`
- `LOG_LEVEL`: default `INFO`
- `LOG_FILE`: default `logs/app.log`

## Security Notes
- DRY_RUN defaults to `true`
- Every order validates the symbol exists in exchange markets first
- API keys should have only TRADE permissions; no WITHDRAW rights

## Notes
- If `<TICKER>/USDC` exists, the bot buys in USDC. Otherwise it checks USDT balance; if insufficient it converts USDC→USDT via `USDC/USDT` market order, then buys `<TICKER>/USDT`.
- Market minimums are respected when available; cost may be adjusted up to the exchange minimum.
- Insufficient funds in both USDC and USDT will result in a message with balances.
- CoinGecko rate limits apply; if icon retrieval fails, the bot falls back to text.
