import unittest

from signal_strategy import sma_crossover_signal
from risk_control import InvalidRiskDataError


class SignalStrategyTests(unittest.TestCase):
    def test_flat_history_holds(self):
        self.assertEqual(sma_crossover_signal([10, 10, 10, 10, 10, 10]), "HOLD")

    def test_bullish_crossover(self):
        self.assertEqual(sma_crossover_signal([10, 10, 10, 10, 10, 20]), "BUY")

    def test_bearish_crossover(self):
        self.assertEqual(sma_crossover_signal([20, 20, 20, 20, 20, 1]), "SELL")

    def test_short_history_rejected(self):
        with self.assertRaises(InvalidRiskDataError):
            sma_crossover_signal([1, 2, 3])

    def test_nonfinite_price_rejected(self):
        with self.assertRaises(InvalidRiskDataError):
            sma_crossover_signal([1, 2, 3, 4, float("nan"), 6])

    def test_overflowing_moving_average_rejected(self):
        with self.assertRaises(InvalidRiskDataError):
            sma_crossover_signal([1e308, 1e308, 1e308, 1e308, 1e308, 1e308])

    def test_invalid_windows_rejected(self):
        with self.assertRaises(InvalidRiskDataError):
            sma_crossover_signal([1, 2, 3, 4, 5, 6], fast_window=5, slow_window=3)


if __name__ == "__main__":
    unittest.main()
