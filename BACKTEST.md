# Historical paper backtest

This tool evaluates the existing SMA-crossover signal generator against historical OHLCV candles. It does not fetch market data, connect to an exchange, or place orders.

## Input CSV

Required columns: `timestamp` (or `open_time`), `open`, `close`. Optional `close_time` lets the loader exclude a candle that was not yet complete at the run's UTC time. Timestamps may be Unix seconds, Unix milliseconds, or ISO-8601 strings with a timezone. Keep rows strictly increasing and unique.

For the recorded BTCUSDT run, 1,000 daily candles were obtained from Binance's public spot klines endpoint. The newest unfinished candle was excluded; the resulting 999 completed candles span 2024-01-30 through 2026-10-24 UTC. Raw market data is intentionally not committed to the repository.

## Run

```bash
python3 backtest.py path/to/BTCUSDT_1d.csv
```

Defaults mirror the paper risk settings: 1,000 USDT virtual capital, 100 USDT maximum notional per position, 5 USDT daily realized-loss stop, 0.1% fee per side, 5 basis points adverse slippage per side, and SMA windows 3/5.

## Methodology and limitations

- The signal at candle *i* is computed from closes strictly before candle *i*; orders are simulated at candle *i*'s open. This avoids using that candle's future close to decide its own entry.
- Buy fills are increased and sell fills decreased by the configured slippage.
- Entry and exit fees are charged; the ending open position is forcibly liquidated at the last candle's close with adverse slippage.
- The benchmark buys one position capped at the same maximum notional on the first candle and holds to the end, using the same fee and slippage assumptions.
- Maximum drawdown uses daily close-marked equity; it does not capture intraday excursions.
- The daily loss stop is based on realized net P&L, as defined by the paper ledger; unrealized losses do not trigger it.
- The output is a historical simulation, not a forecast. Daily candles, simplified fill assumptions, and the chosen date range can materially affect results. No claim of profitability should be made from a single backtest.
