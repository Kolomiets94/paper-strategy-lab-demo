import unittest

from walk_forward import build_walk_forward


class WalkForwardTests(unittest.TestCase):
    def test_selects_only_from_training_years_and_reports_holdout(self):
        report = {"results": [
            {"period": "2024", "status": "ok", "fast_window": 3, "slow_window": 5, "return_pct": 10, "buy_and_hold_return_pct": 2},
            {"period": "2025", "status": "ok", "fast_window": 3, "slow_window": 5, "return_pct": 0, "buy_and_hold_return_pct": -1},
            {"period": "2024", "status": "ok", "fast_window": 10, "slow_window": 20, "return_pct": 5, "buy_and_hold_return_pct": 2},
            {"period": "2025", "status": "ok", "fast_window": 10, "slow_window": 20, "return_pct": 5, "buy_and_hold_return_pct": -1},
            {"period": "2026_ytd", "status": "ok", "fast_window": 3, "slow_window": 5, "return_pct": 4, "buy_and_hold_return_pct": -2},
            {"period": "2026_ytd", "status": "ok", "fast_window": 10, "slow_window": 20, "return_pct": -3, "buy_and_hold_return_pct": -2},
        ]}
        result = build_walk_forward(report)
        self.assertEqual(result["selected_parameters"], {"fast_window": 3, "slow_window": 5})
        self.assertEqual(result["holdout_result"]["return_pct"], 4)
        self.assertEqual(result["training_mean_return_pct"], 5)
        self.assertIn("2026 data used for selection", result["selection_rule"])

    def test_requires_both_training_years(self):
        with self.assertRaises(ValueError):
            build_walk_forward({"results": [
                {"period": "2024", "status": "ok", "fast_window": 3, "slow_window": 5, "return_pct": 1}
            ]})


if __name__ == "__main__":
    unittest.main()
