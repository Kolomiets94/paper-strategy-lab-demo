import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from risk_control import (
    DuplicateExecutionError, InvalidRiskDataError, PaperRiskController,
    RiskConfig, SQLiteRiskLedger,
)


class RiskControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "ledger.sqlite3"
        self.ledger = SQLiteRiskLedger(self.db)
        self.risk = PaperRiskController(self.ledger)
        self.now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_execution_rejected(self):
        self.ledger.record_execution("x1", 2.0, 0.2, self.now)
        with self.assertRaises(DuplicateExecutionError):
            self.ledger.record_execution("x1", 2.0, 0.2, self.now)
        self.assertAlmostEqual(self.ledger.daily_net_pnl_usdt(self.now), 1.8)

    def test_restart_persistence(self):
        self.ledger.record_execution("x1", -1.0, 0.1, self.now)
        reopened = SQLiteRiskLedger(self.db)
        self.assertAlmostEqual(reopened.daily_net_pnl_usdt(self.now), -1.1)

    def test_utc_midnight_boundary(self):
        yesterday = self.now.replace(hour=23, minute=59) - timedelta(days=1)
        midnight = self.now.replace(hour=0, minute=0)
        self.ledger.record_execution("old", -50, 0, yesterday)
        self.ledger.record_execution("today", 1, 0.1, midnight)
        self.assertAlmostEqual(self.ledger.daily_net_pnl_usdt(self.now), 0.9)

    def test_daily_loss_stop_includes_fees(self):
        self.ledger.record_execution("loss", -4.7, 0.3, self.now)
        self.assertFalse(self.risk.can_open_position(50, self.now))

    def test_position_limit(self):
        self.assertTrue(self.risk.can_open_position(100, self.now))
        self.assertFalse(self.risk.can_open_position(100.01, self.now))

    def test_invalid_pnl_rejected(self):
        for execution_id, value in (("nan", float("nan")), ("overflow", 10 ** 1000)):
            with self.subTest(execution_id=execution_id):
                with self.assertRaises(InvalidRiskDataError):
                    self.ledger.record_execution(execution_id, value, 0, self.now)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(InvalidRiskDataError):
            self.ledger.record_execution("naive", 1, 0, datetime(2026, 10, 25))

    def test_invalid_position_fails_closed(self):
        with self.assertRaises(InvalidRiskDataError):
            self.risk.can_open_position(float("nan"), self.now)

    def test_corrupt_ledger_fails_closed(self):
        self.ledger.record_execution("x1", 1, 0, self.now)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE executions SET realized_pnl_usdt = 'not-a-number' WHERE execution_id='x1'")
        with self.assertRaises(InvalidRiskDataError):
            self.ledger.daily_net_pnl_usdt(self.now)

    def test_defaults(self):
        config = RiskConfig()
        self.assertEqual(config.paper_capital_usdt, 1000.0)
        self.assertEqual(config.max_position_notional_usdt, 100.0)
        self.assertEqual(config.daily_loss_limit_usdt, 5.0)


if __name__ == "__main__":
    unittest.main()
