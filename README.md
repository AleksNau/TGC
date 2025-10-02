## Telegram bot with USDC base currency on Bybit

Run:

1. Create `.env` from `.env.example` and fill in `TELEGRAM_TOKEN`, `BYBIT_API_KEY`, `BYBIT_SECRET`.
2. Install deps:

```bash
pip3 install -r requirements.txt
```

3. Start bot:

```bash
python3 -m bot.main
```

Features:
- Prefer `<TICKER>/USDC`; fallback to `<TICKER>/USDT` with auto USDC→USDT conversion
- DRY_RUN mode prints intended actions
- Detailed logging to `logs/bot.log`
