import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from paper_trader import PaperPortfolio
from risk_control import DuplicateExecutionError, InvalidRiskDataError, RiskConfig


class PaperPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "paper.sqlite3"
        self.now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        self.bot = PaperPortfolio(self.db, fee_rate=0.001)

    def tearDown(self):
        self.temp.cleanup()

    def test_buy_sell_lifecycle_accounts_for_fees(self):
        self.assertEqual(self.bot.apply_signal("BTC", "BUY", 50000, self.now), "OPENED")
        self.assertEqual(self.bot.snapshot({"BTC": 50000}, self.now).open_positions, 1)
        result = self.bot.apply_signal("BTC", "SELL", 51000, self.now)
        self.assertTrue(result.startswith("CLOSED net_pnl="))
        snap = self.bot.snapshot({}, self.now)
        self.assertEqual(snap.open_positions, 0)
        self.assertAlmostEqual(snap.realized_net_pnl_today_usdt, 1.798, places=5)

    def test_duplicate_buy_does_not_double_position(self):
        self.assertEqual(self.bot.apply_signal("ETH", "BUY", 2500, self.now), "OPENED")
        self.assertEqual(self.bot.apply_signal("ETH", "BUY", 2500, self.now), "ALREADY_OPEN")
        self.assertEqual(len(self.bot.positions()), 1)

    def test_restart_persists_cash_and_position(self):
        self.bot.apply_signal("SOL", "BUY", 100, self.now)
        reopened = PaperPortfolio(self.db)
        self.assertEqual(len(reopened.positions()), 1)
        self.assertAlmostEqual(reopened.snapshot({"SOL": 100}, self.now).cash_usdt, 899.9)

    def test_daily_loss_stop_blocks_new_entry(self):
        self.bot.ledger.record_execution("loss1", -4.8, 0.2, self.now)
        self.assertEqual(self.bot.apply_signal("BTC", "BUY", 50000, self.now), "RISK_BLOCKED")

    def test_max_notional_is_100(self):
        self.bot.apply_signal("BTC", "BUY", 50000, self.now)
        position = self.bot.positions()["BTC"]
        self.assertAlmostEqual(position.entry_notional_usdt, 100.0)

    def test_invalid_symbol_and_signal_fail_closed(self):
        with self.assertRaises(InvalidRiskDataError):
            self.bot.apply_signal("DOGE", "BUY", 1, self.now)
        with self.assertRaises(InvalidRiskDataError):
            self.bot.apply_signal("BTC", "YOLO", 1, self.now)

    def test_missing_price_for_signal_fails_closed(self):
        with self.assertRaises(InvalidRiskDataError):
            self.bot.process_signals({}, {"BTC": "BUY"}, self.now)

    def test_duplicate_close_execution_is_atomic(self):
        self.bot.apply_signal("BTC", "BUY", 50000, self.now)
        self.bot.ledger.record_execution("used-id", 0.0, 0.0, self.now)
        cash_before = self.bot.snapshot({"BTC": 50000}, self.now).cash_usdt
        with self.assertRaises(DuplicateExecutionError):
            self.bot.apply_signal("BTC", "SELL", 51000, self.now, execution_id="used-id")
        self.assertEqual(len(self.bot.positions()), 1)
        self.assertAlmostEqual(self.bot._cash(), cash_before)
        self.assertAlmostEqual(self.bot.ledger.daily_net_pnl_usdt(self.now), 0.0)

    def test_empty_execution_id_is_rejected_without_closing_position(self):
        self.bot.apply_signal("BTC", "BUY", 50000, self.now)
        with self.assertRaises(InvalidRiskDataError):
            self.bot.apply_signal("BTC", "SELL", 51000, self.now, execution_id="")
        self.assertEqual(len(self.bot.positions()), 1)

    def test_small_cash_sizes_position_to_include_fee(self):
        small_db = Path(self.temp.name) / "small.sqlite3"
        small = PaperPortfolio(small_db, config=RiskConfig(paper_capital_usdt=50.0), fee_rate=0.001)
        self.assertEqual(small.apply_signal("BTC", "BUY", 50000, self.now), "OPENED")
        self.assertLessEqual(small._cash(), 0.000001)
        self.assertGreaterEqual(small._cash(), 0.0)
        self.assertLessEqual(small.positions()["BTC"].entry_notional_usdt, 50.0)

    def test_unknown_signal_symbol_fails_closed(self):
        with self.assertRaises(InvalidRiskDataError):
            self.bot.process_signals({"DOGE": 1.0}, {"DOGE": "BUY"}, self.now)

    def test_hold_does_not_trade(self):
        self.assertEqual(self.bot.apply_signal("BTC", "HOLD", 50000, self.now), "HOLD")
        self.assertEqual(len(self.bot.positions()), 0)

    def test_config_is_paper_capital_1000(self):
        self.assertEqual(RiskConfig().paper_capital_usdt, 1000.0)


if __name__ == "__main__":
    unittest.main()
