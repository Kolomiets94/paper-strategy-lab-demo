import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backtest import Bar, load_bars, run_backtest
from risk_control import InvalidRiskDataError


class HistoricalBacktestTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.bars = [
            Bar(self.start + timedelta(days=i), open=100 + i, close=100 + i)
            for i in range(20)
        ]

    def test_backtest_smoke_and_cash_pnl_reconcile(self):
        result = run_backtest(self.bars, fee_rate=0.001, slippage_bps=5)
        self.assertEqual(result["mode"], "HISTORICAL_PAPER_BACKTEST")
        self.assertEqual(result["bars"], 20)
        self.assertEqual(result["initial_capital_usdt"], 1000)
        self.assertAlmostEqual(
            result["final_equity_usdt"] - result["initial_capital_usdt"],
            result["total_net_pnl_usdt"], places=5
        )
        self.assertGreaterEqual(result["total_fees_usdt"], 0)
        self.assertIn("buy_and_hold_benchmark_net_pnl_usdt", result)
        self.assertEqual(result["data_source"], "Binance Spot public BTCUSDT daily klines")

    def test_signal_only_sees_candles_before_execution_bar(self):
        seen = []

        def record_history(closes, *, fast_window=3, slow_window=5):
            seen.append(list(closes))
            return "HOLD"

        with patch("backtest.sma_crossover_signal", side_effect=record_history):
            run_backtest(self.bars, fast_window=3, slow_window=5)
        self.assertTrue(seen)
        for call_index, history in enumerate(seen, start=6):
            self.assertEqual(history, [bar.close for bar in self.bars[:call_index]])
            self.assertNotEqual(len(history), call_index + 1)

    def test_slippage_and_fees_reduce_round_trip_result(self):
        bars = [
            Bar(self.start + timedelta(days=i), open=100, close=100)
            for i in range(12)
        ]
        # Force a buy on the first evaluated bar, then a sell on the next.
        with patch("backtest.sma_crossover_signal", side_effect=["BUY", "SELL"] + ["HOLD"] * 20):
            result = run_backtest(bars, fee_rate=0.001, slippage_bps=10)
        self.assertEqual(result["closed_trades"], 1)
        self.assertLess(result["total_net_pnl_usdt"], 0)
        self.assertGreater(result["total_fees_usdt"], 0)

    def test_csv_loader_discards_unfinished_candle(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bars.csv"
            now = datetime(2024, 1, 3, tzinfo=timezone.utc)
            rows = [
                {"timestamp": int((now - timedelta(days=9-i)).timestamp() * 1000),
                 "open": str(100+i), "close": str(101+i),
                 "close_time": int((now - timedelta(days=8-i, milliseconds=1)).timestamp() * 1000)}
                for i in range(8)
            ]
            rows.append({"timestamp": int(now.timestamp() * 1000), "open": "108", "close": "109",
                         "close_time": int((now + timedelta(days=1)).timestamp() * 1000)})
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "close", "close_time"])
                writer.writeheader()
                writer.writerows(rows)
            loaded = load_bars(path, as_of=now)
            self.assertEqual(len(loaded), 8)

    def test_duplicate_or_out_of_order_timestamps_rejected(self):
        duplicate = list(self.bars)
        duplicate[4] = Bar(duplicate[3].timestamp, 105, 105)
        with self.assertRaises(InvalidRiskDataError):
            run_backtest(duplicate)

    def test_invalid_cost_configuration_rejected(self):
        with self.assertRaises(InvalidRiskDataError):
            run_backtest(self.bars, slippage_bps=10000)

    def test_nonpositive_prices_rejected(self):
        bad = list(self.bars)
        bad[4] = Bar(bad[4].timestamp, 0, 100)
        with self.assertRaises(InvalidRiskDataError):
            run_backtest(bad)


if __name__ == "__main__":
    unittest.main()
