"""
paper_tracker.py - Real paper trading data recorder for Cupsy.

Records all simulated trades with live market data so you can analyze
actual performance without risking real money. Every price used is
fetched from real Solana DEX markets (DexScreener / Jupiter).

Output files (written to data/):
  paper_trades.csv    - One row per closed trade with full P&L data
  price_history.csv   - Price snapshots every ~30s per open position
  session_stats.json  - Aggregate stats + full trade history (updated live)
"""

import csv
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OpenTrade:
    """An active paper position being monitored with real prices."""
    trade_id: str
    symbol: str
    mint: str
    entry_time: float
    entry_price_usd: float
    sol_spent: float
    token_amount: float
    current_price_usd: float = 0.0
    last_updated: float = field(default_factory=time.time)
    price_snapshots: int = 0

    @property
    def pnl_pct(self) -> float:
        if self.entry_price_usd <= 0 or self.current_price_usd <= 0:
            return 0.0
        return ((self.current_price_usd - self.entry_price_usd) / self.entry_price_usd) * 100

    @property
    def pnl_sol(self) -> float:
        if self.entry_price_usd <= 0 or self.current_price_usd <= 0:
            return 0.0
        ratio = self.current_price_usd / self.entry_price_usd
        return self.sol_spent * ratio - self.sol_spent

    @property
    def age_seconds(self) -> float:
        return time.time() - self.entry_time


@dataclass
class TradeRecord:
    """A fully closed paper trade with real entry/exit prices."""
    trade_id: str
    symbol: str
    mint: str
    entry_time: float
    exit_time: float
    entry_price_usd: float
    exit_price_usd: float
    sol_spent: float
    sol_received: float
    token_amount: float
    pnl_sol: float
    pnl_pct: float
    exit_reason: str
    hold_seconds: float
    price_snapshots_taken: int = 0

    @property
    def won(self) -> bool:
        return self.pnl_sol > 0


@dataclass
class PriceSnapshot:
    timestamp: float
    symbol: str
    mint: str
    price_usd: float
    pnl_pct: float


@dataclass
class SessionStats:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_sol: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    avg_hold_seconds: float = 0.0
    open_positions: int = 0
    tokens_scanned: int = 0
    tokens_bought: int = 0
    session_start: float = field(default_factory=time.time)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    @property
    def avg_pnl_pct(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.total_pnl_sol / (self.total_trades * 0.1)) * 100  # rough estimate


class PaperTracker:
    """
    Singleton tracker for all paper trading activity.

    Call flow:
      paper_tracker.record_buy(position)       ← from trader._paper_buy()
      paper_tracker.update_price(mint, ...)     ← from risk_manager every 30s
      paper_tracker.record_sell(position, ...)  ← from trader._paper_sell()
    """

    def __init__(self) -> None:
        self._open: dict[str, OpenTrade] = {}   # mint -> OpenTrade
        self._closed: list[TradeRecord] = []
        self._price_history: list[PriceSnapshot] = []
        self._unflushed_snapshots: list[PriceSnapshot] = []
        self._stats = SessionStats()
        self._trade_counter = 0
        self._data_dir = "data"
        os.makedirs(self._data_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public recording API
    # ------------------------------------------------------------------

    def record_buy(self, position) -> None:
        """Register a new paper buy. Called by Trader._paper_buy()."""
        self._trade_counter += 1
        trade_id = f"T{self._trade_counter:04d}"

        open_trade = OpenTrade(
            trade_id=trade_id,
            symbol=position.symbol,
            mint=position.mint,
            entry_time=position.buy_timestamp,
            entry_price_usd=position.buy_price_usd,
            sol_spent=position.sol_spent,
            token_amount=position.token_amount,
            current_price_usd=position.buy_price_usd,
            last_updated=time.time(),
        )
        self._open[position.mint] = open_trade
        self._stats.tokens_bought += 1
        self._stats.open_positions = len(self._open)

    def update_price(self, mint: str, symbol: str, price_usd: float) -> None:
        """
        Update the live price for an open position and record a snapshot.
        Called every ~30s by RiskManager._evaluate_position().
        """
        if mint not in self._open:
            return

        trade = self._open[mint]
        trade.current_price_usd = price_usd
        trade.last_updated = time.time()
        trade.price_snapshots += 1

        snapshot = PriceSnapshot(
            timestamp=time.time(),
            symbol=symbol,
            mint=mint,
            price_usd=price_usd,
            pnl_pct=trade.pnl_pct,
        )
        self._price_history.append(snapshot)
        self._unflushed_snapshots.append(snapshot)

        # Flush every 50 snapshots to keep memory lean
        if len(self._unflushed_snapshots) >= 50:
            self._flush_price_snapshots()

    def record_sell(
        self,
        position,
        exit_price_usd: float,
        sol_received: float,
        reason: str,
    ) -> None:
        """
        Record a paper sell and compute final P&L from real prices.
        Called by Trader._paper_sell().
        """
        exit_time = time.time()
        open_trade = self._open.get(position.mint)

        entry_price = open_trade.entry_price_usd if open_trade else position.buy_price_usd
        entry_time = open_trade.entry_time if open_trade else position.buy_timestamp
        snapshots_taken = open_trade.price_snapshots if open_trade else 0

        pnl_sol = sol_received - position.sol_spent
        pnl_pct = (pnl_sol / position.sol_spent) * 100 if position.sol_spent > 0 else 0.0

        record = TradeRecord(
            trade_id=open_trade.trade_id if open_trade else f"T{self._trade_counter:04d}",
            symbol=position.symbol,
            mint=position.mint,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price_usd=entry_price,
            exit_price_usd=exit_price_usd,
            sol_spent=position.sol_spent,
            sol_received=sol_received,
            token_amount=position.token_amount,
            pnl_sol=pnl_sol,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            hold_seconds=exit_time - entry_time,
            price_snapshots_taken=snapshots_taken,
        )

        self._closed.append(record)
        self._open.pop(position.mint, None)

        # Update aggregate stats
        self._stats.total_trades += 1
        self._stats.total_pnl_sol += pnl_sol
        if pnl_sol > 0:
            self._stats.winning_trades += 1
        else:
            self._stats.losing_trades += 1
        if pnl_pct > self._stats.best_trade_pct:
            self._stats.best_trade_pct = pnl_pct
        if pnl_pct < self._stats.worst_trade_pct:
            self._stats.worst_trade_pct = pnl_pct
        if self._closed:
            self._stats.avg_hold_seconds = (
                sum(t.hold_seconds for t in self._closed) / len(self._closed)
            )
        self._stats.open_positions = len(self._open)

        # Persist immediately
        self._append_trade_csv(record)
        self.save_session_json()

    def increment_scanned(self) -> None:
        """Increment the count of tokens seen. Called per token in main loop."""
        self._stats.tokens_scanned += 1

    # ------------------------------------------------------------------
    # Read API (used by dashboard)
    # ------------------------------------------------------------------

    def get_stats(self) -> SessionStats:
        return self._stats

    def get_open_trades(self) -> list[OpenTrade]:
        return list(self._open.values())

    def get_closed_trades(self, limit: int = 20) -> list[TradeRecord]:
        return list(reversed(self._closed))[:limit]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _trade_csv_path(self) -> str:
        return os.path.join(self._data_dir, "paper_trades.csv")

    def _price_csv_path(self) -> str:
        return os.path.join(self._data_dir, "price_history.csv")

    def _session_json_path(self) -> str:
        return os.path.join(self._data_dir, "session_stats.json")

    def _append_trade_csv(self, record: TradeRecord) -> None:
        path = self._trade_csv_path()
        write_header = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "trade_id", "symbol", "mint",
                    "entry_time", "exit_time", "hold_seconds",
                    "entry_price_usd", "exit_price_usd",
                    "sol_spent", "sol_received",
                    "token_amount", "pnl_sol", "pnl_pct",
                    "exit_reason", "price_snapshots_taken",
                ])
            writer.writerow([
                record.trade_id,
                record.symbol,
                record.mint,
                _fmt_time(record.entry_time),
                _fmt_time(record.exit_time),
                f"{record.hold_seconds:.0f}",
                f"{record.entry_price_usd:.10f}",
                f"{record.exit_price_usd:.10f}",
                f"{record.sol_spent:.6f}",
                f"{record.sol_received:.6f}",
                f"{record.token_amount:.2f}",
                f"{record.pnl_sol:.6f}",
                f"{record.pnl_pct:.2f}",
                record.exit_reason,
                record.price_snapshots_taken,
            ])

    def _flush_price_snapshots(self) -> None:
        if not self._unflushed_snapshots:
            return
        path = self._price_csv_path()
        write_header = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["timestamp", "symbol", "mint", "price_usd", "pnl_pct"])
            for s in self._unflushed_snapshots:
                writer.writerow([
                    _fmt_time(s.timestamp),
                    s.symbol,
                    s.mint,
                    f"{s.price_usd:.10f}",
                    f"{s.pnl_pct:.2f}",
                ])
        self._unflushed_snapshots.clear()

    def save_session_json(self) -> None:
        """Write full session stats + trade history to JSON (overwrites each time)."""
        s = self._stats
        data = {
            "session_start": _fmt_time(s.session_start),
            "last_updated": _fmt_time(time.time()),
            "stats": {
                "total_trades": s.total_trades,
                "winning_trades": s.winning_trades,
                "losing_trades": s.losing_trades,
                "win_rate_pct": round(s.win_rate, 1),
                "total_pnl_sol": round(s.total_pnl_sol, 6),
                "best_trade_pct": round(s.best_trade_pct, 2),
                "worst_trade_pct": round(s.worst_trade_pct, 2),
                "avg_hold_seconds": round(s.avg_hold_seconds, 0),
                "tokens_scanned": s.tokens_scanned,
                "tokens_bought": s.tokens_bought,
                "open_positions": s.open_positions,
            },
            "closed_trades": [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "mint": t.mint,
                    "entry_time": _fmt_time(t.entry_time),
                    "exit_time": _fmt_time(t.exit_time),
                    "hold_seconds": int(t.hold_seconds),
                    "entry_price_usd": t.entry_price_usd,
                    "exit_price_usd": t.exit_price_usd,
                    "sol_spent": round(t.sol_spent, 6),
                    "sol_received": round(t.sol_received, 6),
                    "pnl_sol": round(t.pnl_sol, 6),
                    "pnl_pct": round(t.pnl_pct, 2),
                    "exit_reason": t.exit_reason,
                    "price_snapshots_taken": t.price_snapshots_taken,
                    "won": t.won,
                }
                for t in self._closed
            ],
        }
        with open(self._session_json_path(), "w") as f:
            json.dump(data, f, indent=2)

    def final_save(self) -> None:
        """Flush remaining snapshots and save everything. Call on shutdown."""
        self._flush_price_snapshots()
        self.save_session_json()


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# Module-level singleton — import this everywhere
paper_tracker = PaperTracker()
