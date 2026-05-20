"""Phase 1 metric calculations from trade rows."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

STRATEGY_FAMILIES: dict[str, tuple[str, ...]] = {
    "EMA": ("EMACrossoverStrategy", "EMAStrategy", "EmaCrossover"),
    "Momentum": ("MomentumBurstStrategy", "Momentum"),
    "VWAP": ("VWAPDeviationStrategy", "VWAP"),
}


def family_from_strategy_name(name: str | None) -> str:
    if not name:
        return "Unknown"
    upper = name.upper()
    for family, tokens in STRATEGY_FAMILIES.items():
        for t in tokens:
            if t.upper() in upper or t.upper().replace("STRATEGY", "") in upper:
                return family
    if "CONSENSUS" in upper:
        return "Consensus"
    return "Other"


@dataclass
class TradeStats:
    trades: int = 0
    wins: int = 0
    gross_profit: Decimal = Decimal("0")
    gross_loss: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    expectancy_bps: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "expectancy_bps": round(self.expectancy_bps, 2),
            "profit_factor": round(self.profit_factor, 3) if self.profit_factor != float("inf") else None,
            "total_pnl_usdt": round(float(self.total_pnl), 4),
        }


def compute_stats(rows: Sequence[dict]) -> TradeStats:
    stats = TradeStats()
    if not rows:
        return stats

    pnls_bps: list[float] = []
    for r in rows:
        pnl = Decimal(str(r.get("pnl_usdt") or 0))
        notional = Decimal(str(r.get("entry_price") or 0)) * Decimal(str(r.get("quantity") or 0))
        bps = float((pnl / notional) * Decimal("10000")) if notional > 0 else 0.0

        stats.trades += 1
        stats.total_pnl += pnl
        pnls_bps.append(bps)
        if pnl > 0:
            stats.wins += 1
            stats.gross_profit += pnl
        elif pnl < 0:
            stats.gross_loss += abs(pnl)

    stats.win_rate_pct = (stats.wins / stats.trades) * 100 if stats.trades else 0.0
    stats.expectancy_bps = sum(pnls_bps) / len(pnls_bps) if pnls_bps else 0.0
    if stats.gross_loss > 0:
        stats.profit_factor = float(stats.gross_profit / stats.gross_loss)
    elif stats.gross_profit > 0:
        stats.profit_factor = float("inf")
    else:
        stats.profit_factor = 0.0
    return stats


def max_drawdown_pct(equity_curve: Sequence[float], starting_capital: float) -> float:
    if starting_capital <= 0:
        return 0.0
    peak = starting_capital
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def equity_curve_from_trades(rows: Sequence[dict], starting_capital: float) -> list[float]:
    eq = starting_capital
    curve = [eq]
    for r in sorted(rows, key=lambda x: x.get("close_time") or x.get("time")):
        eq += float(r.get("pnl_usdt") or 0)
        curve.append(eq)
    return curve
