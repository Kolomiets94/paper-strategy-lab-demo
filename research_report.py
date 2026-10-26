"""Download public Binance BTCUSDT daily candles and compare fixed SMA settings.

Research-only: reads public market data; never authenticates or places orders.
The parameter grid is exploratory, not an optimization recommendation.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backtest import Bar, run_backtest
from risk_control import InvalidRiskDataError

BASE_URL = "https://data-api.binance.vision/api/v3/klines"
PARAMETERS = ((3, 5), (3, 8), (5, 10), (10, 20), (20, 50))


def fetch_daily_bars(*, start: datetime, end: datetime, timeout: float = 20.0) -> list[Bar]:
    """Fetch completed UTC daily BTCUSDT spot candles with safe pagination."""
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start and end must be timezone-aware and start < end")
    start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cursor = start_ms
    bars: list[Bar] = []
    while cursor < min(end_ms, now_ms):
        query = urlencode({"symbol": "BTCUSDT", "interval": "1d",
                           "startTime": cursor, "endTime": min(end_ms, now_ms),
                           "limit": 1000})
        request = Request(f"{BASE_URL}?{query}", headers={"User-Agent": "paper-strategy-lab/1.0"})
        try:
            with urlopen(request, timeout=timeout) as response:
                rows = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not fetch Binance public candles: {exc}") from exc
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected response from public candle endpoint")
        if not rows:
            break
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                raise RuntimeError("Malformed candle returned by public endpoint")
            open_ms, open_price, close_price, close_ms = int(row[0]), float(row[1]), float(row[4]), int(row[6])
            # Strictly exclude unfinished candles and anything beyond requested end.
            if close_ms < now_ms and open_ms < end_ms:
                bars.append(Bar(datetime.fromtimestamp(open_ms / 1000, timezone.utc),
                                open_price, close_price,
                                datetime.fromtimestamp(close_ms / 1000, timezone.utc)))
        next_cursor = int(rows[-1][0]) + 86_400_000
        if next_cursor <= cursor:
            raise RuntimeError("Candle pagination did not advance")
        cursor = next_cursor
        if len(rows) < 1000:
            break
        time.sleep(0.15)
    # De-duplicate defensively and validate chronological order.
    unique = {bar.timestamp: bar for bar in bars}
    bars = [unique[key] for key in sorted(unique)]
    if len(bars) < 60:
        raise InvalidRiskDataError(f"Only {len(bars)} completed daily candles fetched; need at least 60")
    return bars


def save_csv(bars: list[Bar], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=("timestamp", "open", "close", "close_time"))
        writer.writeheader()
        for bar in bars:
            writer.writerow({"timestamp": bar.timestamp.isoformat(), "open": bar.open,
                             "close": bar.close, "close_time": bar.close_time.isoformat() if bar.close_time else ""})


def build_report(bars: list[Bar]) -> dict:
    periods = (
        ("2024", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)),
        ("2025", datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ("2026_ytd", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc)),
        ("full_history", datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc)),
    )
    results = []
    for period_name, start, end in periods:
        sample = [bar for bar in bars if start <= bar.timestamp < end]
        if len(sample) < 60:
            results.append({"period": period_name, "bars": len(sample), "status": "skipped: fewer than 60 candles"})
            continue
        for fast, slow in PARAMETERS:
            if len(sample) < slow + 2:
                continue
            result = run_backtest(sample, fast_window=fast, slow_window=slow)
            results.append({
                "period": period_name, "bars": result["bars"], "start_utc": result["start_utc"],
                "end_utc": result["end_utc"], "fast_window": fast, "slow_window": slow,
                "return_pct": result["return_pct"], "buy_and_hold_return_pct": result["buy_and_hold_benchmark_return_pct"],
                "max_drawdown_pct": result["max_drawdown_pct"], "closed_trades": result["closed_trades"],
                "win_rate_pct": result["win_rate_pct"], "total_fees_usdt": result["total_fees_usdt"],
                "net_pnl_usdt": result["total_net_pnl_usdt"], "status": "ok",
            })
    return {
        "mode": "RESEARCH_ONLY_HISTORICAL_PAPER_BACKTEST",
        "symbol": "BTCUSDT", "interval": "1d",
        "data_source": "Binance public Spot market-data endpoint",
        "bars_downloaded": len(bars), "data_start_utc": bars[0].timestamp.isoformat(),
        "data_end_utc": bars[-1].timestamp.isoformat(),
        "assumptions": {"initial_capital_usdt": 1000, "max_position_usdt": 100,
                        "daily_loss_stop_usdt": 5, "fee_per_side": 0.001,
                        "slippage_bps_per_side": 5},
        "parameter_grid": [{"fast": f, "slow": s} for f, s in PARAMETERS],
        "results": results,
        "warnings": [
            "Parameter grid is exploratory; selecting the best historical row creates data-snooping risk.",
            "Daily candles do not reveal intraday drawdowns or actual executable prices.",
            "Fees and slippage are modeled assumptions and may differ from realized costs.",
            "This is not a profitability guarantee or a live-trading recommendation.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch public BTCUSDT daily candles and create a paper backtest research report.")
    parser.add_argument("--start", default="2024-01-01", help="UTC start date, YYYY-MM-DD")
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat(), help="UTC end date, exclusive")
    parser.add_argument("--csv", default="data/BTCUSDT_1d.csv", help="Where to save downloaded candle CSV")
    parser.add_argument("--output", default="research-report.json", help="Where to save JSON report")
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    bars = fetch_daily_bars(start=start, end=end)
    save_csv(bars, Path(args.csv))
    report = build_report(bars)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved candles to {args.csv}; report to {args.output}")


if __name__ == "__main__":
    main()
