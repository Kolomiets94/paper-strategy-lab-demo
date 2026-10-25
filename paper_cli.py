"""Manual paper-mode CLI. Prices/signals are user supplied; no market data or orders."""
import argparse
import json
from datetime import datetime, timezone

from paper_trader import PaperPortfolio, SUPPORTED_SYMBOLS


def main():
    parser = argparse.ArgumentParser(description="Spot risk pilot — PAPER ONLY")
    parser.add_argument("--db", default="paper_ledger.sqlite3")
    parser.add_argument("--prices", required=True, help='JSON, e.g. \'{"BTC":50000,"ETH":2500,"SOL":100}\'')
    parser.add_argument("--signals", default="{}", help='JSON, e.g. \'{"BTC":"BUY"}\'')
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    prices = json.loads(args.prices)
    signals = json.loads(args.signals)
    if not isinstance(prices, dict) or not isinstance(signals, dict):
        parser.error("--prices and --signals must be JSON objects")
    now = datetime.now(timezone.utc)
    bot = PaperPortfolio(args.db)
    if args.status_only:
        print(json.dumps(bot.status(prices, now), indent=2))
        return
    outcomes = bot.process_signals(prices, signals, now)
    print(json.dumps({"mode": "PAPER_ONLY", "outcomes": outcomes,
                      "status": bot.status(prices, now)}, indent=2))


if __name__ == "__main__":
    main()
