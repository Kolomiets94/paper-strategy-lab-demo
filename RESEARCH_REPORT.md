# Historical research report

Fetches completed BTCUSDT daily spot candles from Binance's public market-data API (no API key) and runs the existing paper backtest over calendar-year slices and the full available sample. The report compares five fixed SMA parameter pairs against a buy-and-hold benchmark. It never connects to an account or places orders.

Run from the repository root with Python 3.10+:

```bash
python research_report.py
```

Optional date range and output paths:

```bash
python research_report.py --start 2024-01-01 --end 2026-10-26 --csv data/BTCUSDT_1d.csv --output research-report.json
```

The end date is exclusive. Downloaded CSV and JSON report are local outputs and should not be committed unless intentionally needed. The endpoint is public and does not require API keys. The downloader excludes unfinished daily candles.

The JSON report includes return after modeled fees/slippage, buy-and-hold return, daily-close maximum drawdown, trade count, win rate, and fees for each period/parameter pair. It is a research aid, not evidence of future profitability. Comparing many settings on the same history creates data-snooping risk; daily candles also hide intraday excursions and cannot reproduce exact fills.
