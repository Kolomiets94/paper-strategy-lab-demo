"""Simple transparent SMA-crossover signal generator for supplied price history only.

No market-data fetching. Requires caller-provided ordered closes; output is a hint,
not an instruction to trade. The paper risk gate remains authoritative.
"""
from __future__ import annotations

import math
from typing import Sequence

from risk_control import InvalidRiskDataError


def sma_crossover_signal(closes: Sequence[float], *, fast_window: int = 3,
                         slow_window: int = 5) -> str:
    """Return BUY/SELL/HOLD based on the latest fast/slow SMA crossover."""
    if isinstance(fast_window, bool) or isinstance(slow_window, bool):
        raise InvalidRiskDataError("Windows must be integers")
    if not isinstance(fast_window, int) or not isinstance(slow_window, int):
        raise InvalidRiskDataError("Windows must be integers")
    if fast_window < 1 or slow_window <= fast_window:
        raise InvalidRiskDataError("Require 1 <= fast_window < slow_window")
    if not isinstance(closes, (list, tuple)) or len(closes) < slow_window + 1:
        raise InvalidRiskDataError("Need at least slow_window + 1 ordered closes")
    values = []
    for value in closes:
        number = value
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise InvalidRiskDataError("Prices must be finite positive numbers")
        number = float(number)
        if not math.isfinite(number) or number <= 0:
            raise InvalidRiskDataError("Prices must be finite positive numbers")
        values.append(number)

    def means(end: int) -> tuple[float, float]:
        fast = sum(values[end-fast_window:end]) / fast_window
        slow = sum(values[end-slow_window:end]) / slow_window
        if not math.isfinite(fast) or not math.isfinite(slow):
            raise InvalidRiskDataError("Moving averages overflowed")
        return fast, slow

    previous_fast, previous_slow = means(len(values) - 1)
    latest_fast, latest_slow = means(len(values))
    if previous_fast <= previous_slow and latest_fast > latest_slow:
        return "BUY"
    if previous_fast >= previous_slow and latest_fast < latest_slow:
        return "SELL"
    return "HOLD"
