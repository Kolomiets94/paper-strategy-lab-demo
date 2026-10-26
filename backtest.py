"""Historical OHLCV backtest for the paper-only SMA crossover strategy.

Signals are computed using completed candles only and executed at the next candle's
open with adverse slippage. This is research tooling; it never connects to an exchange.
"""
from __future__ import annotations

import argparse
import csv
import json
from contextlib import closing
import math
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from paper_trader import PaperPortfolio
from risk_control import InvalidRiskDataError, RiskConfig
from signal_strategy import sma_crossover_signal


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    close: float
    close_time: datetime | None = None


def _parse_time(value: str) -> datetime:
    raw = value.strip()
    try:
        number = int(raw)
    except ValueError:
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidRiskDataError("Invalid candle timestamp") from exc
        if result.tzinfo is None or result.utcoffset() is None:
            raise InvalidRiskDataError("Candle timestamps must include a timezone")
        return result.astimezone(timezone.utc)
    # Accept Unix timestamps in milliseconds or seconds.
    try:
        return datetime.fromtimestamp(number / (1000 if abs(number) >= 100_000_000_000 else 1),
                                      tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidRiskDataError("Candle timestamp is out of range") from exc


def _price(value: str, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidRiskDataError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise InvalidRiskDataError(f"{label} must be a finite positive number")
    return result


def load_bars(path: str | Path, *, as_of: datetime | None = None) -> list[Bar]:
    """Load CSV with timestamp/open/close columns; optionally discard unfinished bars.

    Optional aliases: open_time for timestamp. If close_time is supplied, bars whose
    close_time is not strictly before as_of are discarded to avoid partial-candle use.
    """
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise InvalidRiskDataError("as_of must be timezone-aware")
    cutoff = as_of.astimezone(timezone.utc) if as_of else None
    bars: list[Bar] = []
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise InvalidRiskDataError("CSV is missing a header")
            fields = {name.strip().lower(): name for name in reader.fieldnames}
            time_field = fields.get("timestamp") or fields.get("open_time") or fields.get("date")
            open_field, close_field = fields.get("open"), fields.get("close")
            close_time_field = fields.get("close_time")
            if not time_field or not open_field or not close_field:
                raise InvalidRiskDataError("CSV requires timestamp/open/close columns")
            for row in reader:
                timestamp = _parse_time(row[time_field])
                close_time = _parse_time(row[close_time_field]) if close_time_field and row.get(close_time_field) else None
                if cutoff and close_time and close_time >= cutoff:
                    continue
                bar = Bar(timestamp, _price(row[open_field], "open"), _price(row[close_field], "close"), close_time)
                if bars and bar.timestamp <= bars[-1].timestamp:
                    raise InvalidRiskDataError("Candle timestamps must be strictly increasing and unique")
                bars.append(bar)
    except OSError as exc:
        raise InvalidRiskDataError(f"Could not read candle CSV: {exc}") from exc
    if len(bars) < 8:
        raise InvalidRiskDataError("Need at least 8 completed candles for a meaningful SMA backtest")
    return bars


def run_backtest(bars: Iterable[Bar], *, capital: float = 1000.0,
                 max_position: float = 100.0, daily_loss_limit: float = 5.0,
                 fee_rate: float = 0.001, slippage_bps: float = 5.0,
                 fast_window: int = 3, slow_window: int = 5,
                 evaluation_start_index: int = 0) -> dict:
    bars = list(bars)
    if len(bars) < slow_window + 2:
        raise InvalidRiskDataError("Not enough candles for the selected SMA windows")
    if isinstance(evaluation_start_index, bool) or not isinstance(evaluation_start_index, int):
        raise InvalidRiskDataError("evaluation_start_index must be an integer")
    if evaluation_start_index < 0 or evaluation_start_index >= len(bars):
        raise InvalidRiskDataError("evaluation_start_index must identify a candle in the input")
    evaluation_bars = bars[evaluation_start_index:]
    if len(evaluation_bars) < slow_window + 2:
        raise InvalidRiskDataError("Not enough evaluation candles for the selected SMA windows")
    if isinstance(fast_window, bool) or isinstance(slow_window, bool) or not isinstance(fast_window, int) or not isinstance(slow_window, int):
        raise InvalidRiskDataError("SMA windows must be integers")
    if fast_window < 1 or slow_window <= fast_window:
        raise InvalidRiskDataError("Require 1 <= fast_window < slow_window")
    for name, value in (("capital", capital), ("max_position", max_position),
                        ("daily_loss_limit", daily_loss_limit), ("fee_rate", fee_rate),
                        ("slippage_bps", slippage_bps)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise InvalidRiskDataError(f"{name} must be finite")
    if capital <= 0 or max_position <= 0 or daily_loss_limit <= 0 or not 0 <= fee_rate < 1 or not 0 <= slippage_bps < 10000:
        raise InvalidRiskDataError("Invalid backtest risk/cost configuration")
    for i, bar in enumerate(bars):
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise InvalidRiskDataError("Candle timestamps must be timezone-aware")
        if i and bar.timestamp <= bars[i-1].timestamp:
            raise InvalidRiskDataError("Candle timestamps must be strictly increasing")
        _price(bar.open, "open")
        _price(bar.close, "close")

    config = RiskConfig(paper_capital_usdt=float(capital),
                        max_position_notional_usdt=float(max_position),
                        daily_loss_limit_usdt=float(daily_loss_limit))
    slip = float(slippage_bps) / 10_000.0
    equity_curve = [float(capital)]
    execution_times: list[datetime] = []
    trade_results: list[float] = []

    with tempfile.TemporaryDirectory(prefix="paper-sma-backtest-") as temp_dir:
        db_path = Path(temp_dir) / "ledger.sqlite3"
        portfolio = PaperPortfolio(db_path, config=config, fee_rate=float(fee_rate))
        # At candle i's open, only candles strictly before i are known. The signal
        # therefore uses bars[:i], then trades at the next candle's open.
        for i in range(max(slow_window + 1, evaluation_start_index), len(bars)):
            history = [bar.close for bar in bars[:i]]
            signal = sma_crossover_signal(history, fast_window=fast_window, slow_window=slow_window)
            bar = bars[i]
            # Refresh the open-position mark at the execution bar's open before
            # checking the daily loss stop or processing the strategy signal.
            portfolio.snapshot({"BTC": bar.open}, bar.timestamp)
            fill = bar.open * (1 + slip) if signal == "BUY" else bar.open * (1 - slip)
            result = portfolio.apply_signal("BTC", signal, fill, bar.timestamp,
                                            execution_id=f"bt-{i}-{signal.lower()}")
            if result.startswith("CLOSED "):
                with closing(sqlite3.connect(db_path)) as con:
                    row = con.execute(
                        "SELECT realized_pnl_usdt, fees_usdt FROM executions WHERE execution_id=?",
                        (f"bt-{i}-{signal.lower()}",),
                    ).fetchone()
                if row:
                    trade_results.append(float(row[0]) - float(row[1]))
                    execution_times.append(bar.timestamp)
            # Mark at the completed candle close for the equity curve and for
            # the next bar's risk gate. Intraday losses remain unobserved by daily bars.
            snapshot = portfolio.snapshot({"BTC": bar.close}, bar.timestamp)
            equity = snapshot.equity_usdt
            if not math.isfinite(equity) or equity < 0:
                raise InvalidRiskDataError("Backtest equity became invalid")
            equity_curve.append(equity)

        # Liquidate any remaining position at the final candle's close, with adverse
        # slippage. This is a forced end-of-test close, not a strategy-generated signal.
        final = bars[-1]
        positions = portfolio.positions()
        if "BTC" in positions:
            end_time = final.close_time or (final.timestamp + timedelta(days=1) - timedelta(microseconds=1))
            fill = final.close * (1 - slip)
            result = portfolio.apply_signal("BTC", "SELL", fill, end_time,
                                            execution_id="bt-final-liquidation")
            if result.startswith("CLOSED "):
                with sqlite3.connect(db_path) as con:
                    row = con.execute(
                        "SELECT realized_pnl_usdt, fees_usdt FROM executions WHERE execution_id=?",
                        ("bt-final-liquidation",),
                    ).fetchone()
                if row:
                    trade_results.append(float(row[0]) - float(row[1]))
                    execution_times.append(end_time)
        final_equity = portfolio._cash()
        equity_curve.append(final_equity)
        with sqlite3.connect(db_path) as con:
            rows = con.execute("SELECT realized_pnl_usdt, fees_usdt FROM executions").fetchall()
        total_fees = sum(float(fee) for _, fee in rows)
        total_net_pnl = sum(float(pnl) - float(fee) for pnl, fee in rows)
        if abs((final_equity - capital) - total_net_pnl) > 1e-6:
            raise InvalidRiskDataError("Cash balance and realized ledger P&L do not reconcile")

    peak = float(capital)
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, (equity - peak) / peak)
    wins = sum(1 for pnl in trade_results if pnl > 0)

    # Benchmark: buy one capped spot position at the first bar's open and hold
    # until the final close, with the same fee and adverse-slippage assumptions.
    benchmark_notional = min(float(max_position), float(capital) / (1.0 + float(fee_rate)))
    benchmark_entry = evaluation_bars[0].open * (1.0 + slip)
    benchmark_qty = benchmark_notional / benchmark_entry
    benchmark_entry_fee = benchmark_notional * float(fee_rate)
    benchmark_exit = evaluation_bars[-1].close * (1.0 - slip)
    benchmark_proceeds = benchmark_qty * benchmark_exit
    benchmark_exit_fee = benchmark_proceeds * float(fee_rate)
    benchmark_net_pnl = benchmark_proceeds - benchmark_notional - benchmark_entry_fee - benchmark_exit_fee
    benchmark_final_equity = float(capital) + benchmark_net_pnl

    return {
        "mode": "HISTORICAL_PAPER_BACKTEST",
        "data_source": "Binance Spot public BTCUSDT daily klines",
        "symbol": "BTCUSDT",
        "bars": len(evaluation_bars),
        "warmup_bars": evaluation_start_index,
        "start_utc": evaluation_bars[0].timestamp.isoformat(),
        "end_utc": evaluation_bars[-1].timestamp.isoformat(),
        "initial_capital_usdt": round(float(capital), 6),
        "final_equity_usdt": round(final_equity, 6),
        "total_net_pnl_usdt": round(total_net_pnl, 6),
        "return_pct": round((final_equity / capital - 1) * 100, 6),
        "buy_and_hold_benchmark_net_pnl_usdt": round(benchmark_net_pnl, 6),
        "buy_and_hold_benchmark_return_pct": round((benchmark_final_equity / capital - 1) * 100, 6),
        "buy_and_hold_benchmark_total_fees_usdt": round(benchmark_entry_fee + benchmark_exit_fee, 6),
        "max_drawdown_method": "daily close-marked equity; intraday excursions are not captured",
        "closed_trades": len(trade_results),
        "winning_trades": wins,
        "win_rate_pct": round(wins / len(trade_results) * 100, 4) if trade_results else 0.0,
        "total_fees_usdt": round(total_fees, 6),
        "fee_rate_per_side": float(fee_rate),
        "slippage_bps_per_side": float(slippage_bps),
        "max_drawdown_pct": round(max_drawdown * 100, 6),
        "daily_loss_stop_usdt": -float(daily_loss_limit),
        "max_position_notional_usdt": float(max_position),
        "fast_window": fast_window,
        "slow_window": slow_window,
        "note": "Signals use completed candles only; fills occur at the next candle open with modeled slippage. Final open position is force-liquidated at the last close.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical BTC spot SMA paper backtest; no exchange orders.")
    parser.add_argument("csv", help="CSV with timestamp/open/close columns; optional close_time")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--max-position", type=float, default=100.0)
    parser.add_argument("--daily-loss-limit", type=float, default=5.0)
    parser.add_argument("--fee-rate", type=float, default=0.001, help="fee per side; default 0.001 = 0.1%%")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="adverse slippage per side in basis points")
    parser.add_argument("--fast-window", type=int, default=3)
    parser.add_argument("--slow-window", type=int, default=5)
    args = parser.parse_args()
    bars = load_bars(args.csv, as_of=datetime.now(timezone.utc))
    print(json.dumps(run_backtest(bars, capital=args.capital, max_position=args.max_position,
                                  daily_loss_limit=args.daily_loss_limit, fee_rate=args.fee_rate,
                                  slippage_bps=args.slippage_bps, fast_window=args.fast_window,
                                  slow_window=args.slow_window), indent=2))


if __name__ == "__main__":
    main()
