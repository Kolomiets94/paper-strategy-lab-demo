"""Small walk-forward selector over the existing historical report.

Selects one fixed SMA pair using only 2024-2025 results, then reports that
pair's already-computed 2026 YTD result as a holdout. No live trading.
"""
from __future__ import annotations

import json
from pathlib import Path
import argparse


def build_walk_forward(report: dict) -> dict:
    rows = report.get("results", [])
    training_periods = {"2024", "2025"}
    training: dict[tuple[int, int], list[float]] = {}
    for row in rows:
        if row.get("status") == "ok" and row.get("period") in training_periods:
            key = (int(row["fast_window"]), int(row["slow_window"]))
            training.setdefault(key, []).append(float(row["return_pct"]))
    eligible = {key: values for key, values in training.items() if len(values) == 2}
    if not eligible:
        raise ValueError("Need valid 2024 and 2025 results for every candidate")
    # Predeclare selection rule: maximize the simple mean of the two training-year returns.
    selected, training_returns = max(
        eligible.items(), key=lambda item: sum(item[1]) / len(item[1])
    )
    holdout = next((row for row in rows
                    if row.get("status") == "ok" and row.get("period") == "2026_ytd"
                    and int(row["fast_window"]) == selected[0]
                    and int(row["slow_window"]) == selected[1]), None)
    if holdout is None:
        raise ValueError("No 2026 YTD holdout result exists for the selected parameters")
    return {
        "mode": "RESEARCH_ONLY_WALK_FORWARD_HOLDOUT",
        "selection_rule": "Choose SMA pair with highest arithmetic mean of 2024 and 2025 net returns; no 2026 data used for selection.",
        "training_periods": ["2024", "2025"],
        "training_mean_return_pct": round(sum(training_returns) / len(training_returns), 6),
        "selected_parameters": {"fast_window": selected[0], "slow_window": selected[1]},
        "holdout_period": "2026_ytd",
        "holdout_result": holdout,
        "benchmark_return_pct": holdout.get("buy_and_hold_return_pct"),
        "warnings": [
            "Holdout is only one calendar-year-to-date segment and is not proof of future profitability.",
            "Annual slices currently start with their first in-period candle; SMA warmup from preceding candles is not yet included, so the first eligible signal is delayed.",
            "The fixed parameter grid and costs are assumptions; repeated research choices can still create selection bias.",
            "Daily-close drawdown does not capture intraday losses. Paper-only; no real orders are placed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Select SMA parameters on 2024-2025 and report 2026 YTD holdout.")
    parser.add_argument("report", nargs="?", default="research-report.json")
    parser.add_argument("--output", default="walk-forward-report.json")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    result = build_walk_forward(report)
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
