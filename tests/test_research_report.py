import unittest
from datetime import datetime, timedelta, timezone

from backtest import Bar
from research_report import PARAMETERS, build_report


class ResearchReportTests(unittest.TestCase):
    def test_report_compares_parameter_grid_and_benchmark(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [
            Bar(start + timedelta(days=i), open=100 + i * 0.2, close=100 + i * 0.2)
            for i in range(75)
        ]
        report = build_report(bars)
        self.assertEqual(report["mode"], "RESEARCH_ONLY_HISTORICAL_PAPER_BACKTEST")
        self.assertEqual(report["bars_downloaded"], 75)
        full_history = [row for row in report["results"] if row["period"] == "full_history"]
        self.assertEqual(len(full_history), len(PARAMETERS))
        for row in full_history:
            self.assertIn("return_pct", row)
            self.assertIn("buy_and_hold_return_pct", row)
            self.assertIn("max_drawdown_pct", row)
            self.assertEqual(row["status"], "ok")

    def test_report_documents_data_snooping_warning(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        bars = [Bar(start + timedelta(days=i), 100 + i, 100 + i) for i in range(65)]
        report = build_report(bars)
        self.assertTrue(any("data-snooping" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
