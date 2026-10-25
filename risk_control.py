"""Paper-only risk gate with a persistent SQLite execution ledger.

No exchange connectivity, credentials, order submission, or live trading.
"""
from __future__ import annotations

import math
from contextlib import closing
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path


class RiskError(RuntimeError):
    """Base class for fail-closed risk errors."""


class LedgerUnavailableError(RiskError):
    pass


class InvalidRiskDataError(RiskError):
    pass


class DuplicateExecutionError(RiskError):
    pass


@dataclass(frozen=True)
class RiskConfig:
    paper_capital_usdt: float = 1000.0
    max_position_notional_usdt: float = 100.0
    daily_loss_limit_usdt: float = 5.0


class SQLiteRiskLedger:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        try:
            with closing(self._connect()) as con, con:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("""CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    realized_pnl_usdt REAL NOT NULL,
                    fees_usdt REAL NOT NULL,
                    executed_at_utc TEXT NOT NULL
                )""")
                con.execute("CREATE INDEX IF NOT EXISTS idx_exec_time ON executions(executed_at_utc)")
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not initialize SQLite risk ledger") from exc

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=5.0)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise InvalidRiskDataError("Timestamp must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _finite(value, label: str, *, nonnegative: bool = False) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRiskDataError(f"{label} must be a finite number")
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise InvalidRiskDataError(f"Invalid {label}") from exc
        if not math.isfinite(result) or (nonnegative and result < 0):
            raise InvalidRiskDataError(f"Invalid {label}")
        return result

    def record_execution(self, execution_id: str, realized_pnl_usdt: float,
                         fees_usdt: float, executed_at: datetime) -> None:
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise InvalidRiskDataError("execution_id must be non-empty")
        pnl = self._finite(realized_pnl_usdt, "realized P&L")
        fees = self._finite(fees_usdt, "fees", nonnegative=True)
        timestamp = self._timestamp(executed_at)
        try:
            with closing(self._connect()) as con, con:
                con.execute("INSERT INTO executions VALUES (?, ?, ?, ?)",
                            (execution_id.strip(), pnl, fees, timestamp))
        except sqlite3.IntegrityError as exc:
            raise DuplicateExecutionError(f"Duplicate execution ID: {execution_id}") from exc
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not write execution ledger") from exc

    def daily_net_pnl_usdt(self, now: datetime) -> float:
        current = datetime.fromisoformat(self._timestamp(now))
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        try:
            with closing(self._connect()) as con, con:
                rows = con.execute(
                    "SELECT execution_id, realized_pnl_usdt, fees_usdt FROM executions "
                    "WHERE executed_at_utc >= ? AND executed_at_utc < ?",
                    (start.isoformat(timespec="microseconds"), end.isoformat(timespec="microseconds")),
                ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not read execution ledger") from exc
        total = 0.0
        for execution_id, pnl, fees in rows:
            if not isinstance(execution_id, str) or not execution_id:
                raise InvalidRiskDataError("Invalid execution ID in ledger")
            total += self._finite(pnl, "stored P&L") - self._finite(fees, "stored fees", nonnegative=True)
        if not math.isfinite(total):
            raise InvalidRiskDataError("Daily net P&L is not finite")
        return total


class PaperRiskController:
    @staticmethod
    def _finite(value, label: str, *, nonnegative: bool = False) -> float:
        return SQLiteRiskLedger._finite(value, label, nonnegative=nonnegative)

    def __init__(self, ledger: SQLiteRiskLedger, config: RiskConfig = RiskConfig(),
                 fee_rate: float = 0.001):
        self.ledger = ledger
        self.config = config
        self.fee_rate = self._finite(fee_rate, "fee rate", nonnegative=True)
        if self.fee_rate >= 1:
            raise InvalidRiskDataError("fee rate must be in [0, 1)")
        for name, value in (("capital", config.paper_capital_usdt),
                            ("position cap", config.max_position_notional_usdt),
                            ("daily loss limit", config.daily_loss_limit_usdt)):
            self._finite(value, name, nonnegative=True)
            if value <= 0:
                raise InvalidRiskDataError(f"{name} must be positive")

    def daily_risk_pnl_usdt(self, now: datetime) -> float:
        """Realized net P&L today plus conservative net mark-to-market P&L.

        Open positions without a saved mark use entry price, so entry/estimated
        exit fees are still included. All open-position unrealized P&L is counted,
        even for positions opened on prior days; this is deliberately conservative.
        """
        total = self.ledger.daily_net_pnl_usdt(now)
        try:
            with closing(self.ledger._connect()) as con, con:
                tables = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if "paper_positions" not in tables:
                    return total
                if "mark_prices" in tables:
                    rows = con.execute(
                        "SELECT p.quantity, p.entry_notional_usdt, p.entry_fee_usdt, "
                        "COALESCE(m.price_usdt, p.entry_price) "
                        "FROM paper_positions p LEFT JOIN mark_prices m ON m.symbol=p.symbol"
                    ).fetchall()
                else:
                    rows = con.execute(
                        "SELECT quantity, entry_notional_usdt, entry_fee_usdt, entry_price "
                        "FROM paper_positions"
                    ).fetchall()
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not value open paper positions for risk") from exc
        for quantity, entry_notional, entry_fee, mark_price in rows:
            qty = self._finite(quantity, "stored position quantity", nonnegative=True)
            notional = self._finite(entry_notional, "stored position notional", nonnegative=True)
            entry_fee_value = self._finite(entry_fee, "stored entry fee", nonnegative=True)
            mark = self._finite(mark_price, "stored mark price", nonnegative=True)
            if qty <= 0 or mark <= 0:
                raise InvalidRiskDataError("Invalid open-position valuation data")
            market_value = qty * mark
            unrealized_net = market_value - notional - entry_fee_value - market_value * self.fee_rate
            total += self._finite(unrealized_net, "unrealized net P&L")
        return self._finite(total, "daily risk P&L")

    def can_open_position(self, notional_usdt: float, now: datetime) -> bool:
        """Return False when limits are hit; raise on uncertainty (caller must block)."""
        notional = self._finite(notional_usdt, "position notional", nonnegative=True)
        if notional <= 0:
            raise InvalidRiskDataError("Position notional must be positive")
        if notional > self.config.max_position_notional_usdt or notional > self.config.paper_capital_usdt:
            return False
        daily_pnl = self.daily_risk_pnl_usdt(now)
        return daily_pnl > -self.config.daily_loss_limit_usdt
