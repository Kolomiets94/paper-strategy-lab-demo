"""Deterministic paper-only BTC/ETH/SOL portfolio simulator.

This module never connects to an exchange and never submits orders. Callers provide
synthetic or historical prices and signals explicitly. All entries pass the SQLite
risk gate; closes are recorded once using unique execution IDs.
"""
from __future__ import annotations

import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from risk_control import (
    DuplicateExecutionError,
    InvalidRiskDataError,
    LedgerUnavailableError,
    PaperRiskController,
    RiskConfig,
    SQLiteRiskLedger,
)


SUPPORTED_SYMBOLS = ("BTC", "ETH", "SOL")


@dataclass(frozen=True)
class PaperPosition:
    symbol: str
    quantity: float
    entry_price: float
    entry_notional_usdt: float
    entry_fee_usdt: float
    opened_at: str


@dataclass(frozen=True)
class PaperSnapshot:
    cash_usdt: float
    equity_usdt: float
    open_positions: int
    realized_net_pnl_today_usdt: float
    trading_blocked: bool


class PaperPortfolio:
    """A small spot-only paper portfolio with persisted fills and positions."""

    def __init__(self, db_path: str | Path, config: RiskConfig = RiskConfig(),
                 fee_rate: float = 0.001):
        self.db_path = str(db_path)
        self.config = config
        self.ledger = SQLiteRiskLedger(db_path)
        self.risk = PaperRiskController(self.ledger, config)
        if isinstance(fee_rate, bool) or not isinstance(fee_rate, (int, float)) or not math.isfinite(float(fee_rate)) or fee_rate < 0 or fee_rate >= 1:
            raise InvalidRiskDataError("fee_rate must be finite and in [0, 1)")
        self.fee_rate = float(fee_rate)
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as con:
                con.execute("""CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    cash_usdt REAL NOT NULL
                )""")
                con.execute("INSERT OR IGNORE INTO paper_account(id, cash_usdt) VALUES (1, ?)",
                            (float(config.paper_capital_usdt),))
                con.execute("""CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_notional_usdt REAL NOT NULL,
                    entry_fee_usdt REAL NOT NULL,
                    opened_at TEXT NOT NULL
                )""")
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not initialize paper portfolio") from exc

    @staticmethod
    def _now(now: datetime) -> str:
        return SQLiteRiskLedger._timestamp(now)

    @staticmethod
    def _number(value, label: str, *, positive: bool = False) -> float:
        number = SQLiteRiskLedger._finite(value, label, nonnegative=not positive)
        if positive and number <= 0:
            raise InvalidRiskDataError(f"{label} must be positive")
        return number

    def _connect(self):
        try:
            return sqlite3.connect(self.db_path, timeout=5.0)
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Paper database unavailable") from exc

    def positions(self) -> dict[str, PaperPosition]:
        try:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT symbol, quantity, entry_price, entry_notional_usdt, entry_fee_usdt, opened_at FROM paper_positions"
                ).fetchall()
            result = {}
            for row in rows:
                symbol, qty, price, notional, fee, opened = row
                if symbol not in SUPPORTED_SYMBOLS:
                    raise InvalidRiskDataError("Unknown symbol in persisted positions")
                result[symbol] = PaperPosition(symbol, self._number(qty, "stored quantity", positive=True),
                    self._number(price, "stored entry price", positive=True),
                    self._number(notional, "stored notional", positive=True),
                    self._number(fee, "stored fee"), str(opened))
            return result
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not read paper positions") from exc

    def _cash(self) -> float:
        try:
            with self._connect() as con:
                row = con.execute("SELECT cash_usdt FROM paper_account WHERE id=1").fetchone()
            if row is None:
                raise InvalidRiskDataError("Paper cash account is missing")
            cash = self._number(row[0], "paper cash")
            return cash
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not read paper cash") from exc

    def apply_signal(self, symbol: str, signal: str, price_usdt: float,
                     now: datetime, *, execution_id: str | None = None) -> str:
        """Apply BUY, SELL, or HOLD. SELL closes the whole paper position."""
        if symbol not in SUPPORTED_SYMBOLS:
            raise InvalidRiskDataError(f"Unsupported spot symbol: {symbol}")
        if signal not in ("BUY", "SELL", "HOLD"):
            raise InvalidRiskDataError("signal must be BUY, SELL, or HOLD")
        price = self._number(price_usdt, "price", positive=True)
        timestamp = self._now(now)
        if signal == "HOLD":
            return "HOLD"
        positions = self.positions()
        if signal == "BUY":
            if symbol in positions:
                return "ALREADY_OPEN"
            # Fixed notional makes the max position cap easy to audit.
            notional = min(self.config.max_position_notional_usdt, self._cash())
            if not self.risk.can_open_position(notional, now):
                return "RISK_BLOCKED"
            fee = notional * self.fee_rate
            if self._cash() < notional + fee:
                return "INSUFFICIENT_CASH"
            quantity = notional / price
            try:
                with self._connect() as con:
                    # Recheck uniqueness inside transaction to avoid duplicate concurrent entries.
                    if con.execute("SELECT 1 FROM paper_positions WHERE symbol=?", (symbol,)).fetchone():
                        return "ALREADY_OPEN"
                    con.execute("UPDATE paper_account SET cash_usdt=cash_usdt-? WHERE id=1", (notional + fee,))
                    con.execute("INSERT INTO paper_positions VALUES (?, ?, ?, ?, ?, ?)",
                                (symbol, quantity, price, notional, fee, timestamp))
                return "OPENED"
            except (sqlite3.Error, OSError) as exc:
                raise LedgerUnavailableError("Could not open paper position") from exc

        position = positions.get(symbol)
        if position is None:
            return "NO_POSITION"
        gross_proceeds = position.quantity * price
        exit_fee = gross_proceeds * self.fee_rate
        # Entry fee is charged to cash at entry; both entry and exit fees count against realized P&L.
        gross_pnl = gross_proceeds - position.entry_notional_usdt
        total_fees = position.entry_fee_usdt + exit_fee
        net_pnl = gross_pnl - total_fees
        exec_id = execution_id or f"paper-{uuid.uuid4().hex}"
        try:
            # Ledger entry, cash update, and position removal share one SQLite
            # transaction: a crash cannot leave a recorded fill with an open position.
            with self._connect() as con:
                con.execute(
                    "INSERT INTO executions (execution_id, realized_pnl_usdt, fees_usdt, executed_at_utc) VALUES (?, ?, ?, ?)",
                    (exec_id.strip(), SQLiteRiskLedger._finite(gross_pnl, "realized P&L"),
                     SQLiteRiskLedger._finite(total_fees, "fees", nonnegative=True), timestamp),
                )
                con.execute("UPDATE paper_account SET cash_usdt=cash_usdt+? WHERE id=1",
                            (gross_proceeds - exit_fee,))
                deleted = con.execute("DELETE FROM paper_positions WHERE symbol=?", (symbol,)).rowcount
                if deleted != 1:
                    raise InvalidRiskDataError("Position changed during close; transaction rolled back")
        except sqlite3.IntegrityError as exc:
            raise DuplicateExecutionError(f"Duplicate execution ID: {exec_id}") from exc
        except InvalidRiskDataError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise LedgerUnavailableError("Could not close paper position cleanly") from exc
        return f"CLOSED net_pnl={net_pnl:.4f} USDT"

    def process_signals(self, prices: Mapping[str, float], signals: Mapping[str, str],
                        now: datetime) -> dict[str, str]:
        """Process only supplied symbols/signals; missing symbols are ignored."""
        outcomes = {}
        for symbol in SUPPORTED_SYMBOLS:
            if symbol in signals:
                if symbol not in prices:
                    raise InvalidRiskDataError(f"Missing price for signaled symbol {symbol}")
                outcomes[symbol] = self.apply_signal(symbol, signals[symbol], prices[symbol], now)
        return outcomes

    def snapshot(self, prices: Mapping[str, float], now: datetime) -> PaperSnapshot:
        positions = self.positions()
        market_value = 0.0
        for symbol, position in positions.items():
            if symbol not in prices:
                raise InvalidRiskDataError(f"Missing mark price for open position {symbol}")
            market_value += position.quantity * self._number(prices[symbol], "mark price", positive=True)
        cash = self._cash()
        equity = cash + market_value
        if not math.isfinite(equity) or equity < 0:
            raise InvalidRiskDataError("Invalid paper equity")
        daily_pnl = self.ledger.daily_net_pnl_usdt(now)
        return PaperSnapshot(cash, equity, len(positions), daily_pnl,
                             daily_pnl <= -self.config.daily_loss_limit_usdt)

    def status(self, prices: Mapping[str, float], now: datetime) -> dict:
        snap = self.snapshot(prices, now)
        return {
            "mode": "PAPER_ONLY",
            "symbols": list(SUPPORTED_SYMBOLS),
            "cash_usdt": round(snap.cash_usdt, 6),
            "equity_usdt": round(snap.equity_usdt, 6),
            "open_positions": snap.open_positions,
            "realized_net_pnl_today_usdt": round(snap.realized_net_pnl_today_usdt, 6),
            "daily_stop_usdt": -self.config.daily_loss_limit_usdt,
            "max_position_notional_usdt": self.config.max_position_notional_usdt,
            "trading_blocked": snap.trading_blocked,
        }
