# Spot Risk Pilot — paper-only simulator

This is a local, deterministic paper portfolio, not a live trading bot. It has no exchange client, network requests, API keys, order placement, deployment, or GitHub Actions.

## Limits

- Initial paper capital: 1000 USDT
- Maximum position notional: 100 USDT per symbol
- Daily realized net-loss stop: -5 USDT, fees included
- Symbols: BTC, ETH, SOL spot only; no leverage
- SQLite persists the ledger, cash balance, and open positions
- Invalid input or database failures must be treated as a hard block

## Run tests

Python 3.10+; from repository root:

```bash
python -m unittest discover -s tests -v
```

## Run a manual paper simulation

Prices and signals are supplied manually. They are not live market data.

```bash
python paper_cli.py --prices '{"BTC":50000,"ETH":2500,"SOL":100}' --signals '{"BTC":"BUY"}'
python paper_cli.py --prices '{"BTC":51000,"ETH":2500,"SOL":100}' --signals '{"BTC":"SELL"}'
python paper_cli.py --prices '{"BTC":51000,"ETH":2500,"SOL":100}' --status-only
```

The default database file is `paper_ledger.sqlite3`; keep it private/local and do not commit runtime ledger data.

## Important accounting notes

The simulator uses a fixed 0.1% fee rate by default. BUY targets up to 100 USDT notional and automatically sizes down when the remaining cash cannot cover both notional and fee. SELL realizes gross price P&L and records both entry and exit fees in the risk ledger. Daily stop is applied to realized net P&L on UTC calendar days. Unrealized P&L is not included in the daily realized stop.

Unsupported signal symbols and invalid price history are rejected rather than silently ignored. The SMA helper also rejects overflowed moving-average calculations.

This is a prototype intended for testing and education, not financial advice or production-grade trading software.
