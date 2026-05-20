"""Global pre-trade market quality filters (spread, vol, chop, fees)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Deque, Optional

from indicators import atr_from_ticks, chop_ratio, spread_bps, volume_sum

D = Decimal


@dataclass
class FilterParams:
    max_spread_bps: D = D("10")
    min_atr_pct: D = D("0.0006")
    max_atr_pct: D = D("0.025")
    min_volume_window: D = D("0")
    volume_window_ms: int = 3000
    max_chop_ratio: D = D("6")
    commission_rate: D = D("0.001")
    min_edge_vs_fees_mult: D = D("2.5")
    min_expected_move_bps: D = D("12")


@dataclass
class MarketSnapshot:
    atr: D
    atr_pct: D
    spread_bps: D
    volume_window: D
    chop: D
    mid_price: D


def build_snapshot(tick: dict, tick_history: Deque) -> MarketSnapshot:
    price = tick["price"]
    bid = tick.get("bid_price") or D("0")
    ask = tick.get("ask_price") or D("0")
    atr = atr_from_ticks(tick_history, 14)
    atr_pct = (atr / price) if price > ZERO else D("0")
    if price <= ZERO:
        spr = D("9999")
        return MarketSnapshot(
            atr=ZERO,
            atr_pct=ZERO,
            spread_bps=spr,
            volume_window=ZERO,
            chop=D("999"),
            mid_price=ZERO,
        )
    spr = spread_bps(bid, ask) if bid > ZERO and ask > ZERO else D("0")
    vol = volume_sum(tick_history, 3000, tick["timestamp_ms"])
    prices = [t["price"] for t in tick_history]
    chop = chop_ratio(prices, atr, window=30) if atr > ZERO else D("999")
    return MarketSnapshot(
        atr=atr,
        atr_pct=atr_pct,
        spread_bps=spr,
        volume_window=vol,
        chop=chop,
        mid_price=price,
    )


ZERO = D("0")


def passes_market_filters(
    snapshot: MarketSnapshot,
    params: FilterParams,
    expected_move_bps: Optional[D] = None,
) -> tuple[bool, str]:
    if snapshot.spread_bps > params.max_spread_bps:
        return False, f"spread {snapshot.spread_bps:.2f}bps > max {params.max_spread_bps}"

    if snapshot.atr_pct < params.min_atr_pct:
        return False, f"ATR% {snapshot.atr_pct:.6f} below min (dead market)"

    if snapshot.atr_pct > params.max_atr_pct:
        return False, f"ATR% {snapshot.atr_pct:.6f} above max (chaos)"

    if snapshot.volume_window < params.min_volume_window:
        return False, "insufficient tick volume in window"

    if snapshot.chop > params.max_chop_ratio:
        return False, f"chop ratio {snapshot.chop:.2f} (range-bound noise)"

    move_bps = expected_move_bps or params.min_expected_move_bps
    round_trip_fee_bps = params.commission_rate * D("2") * D("10000")
    min_edge = round_trip_fee_bps * params.min_edge_vs_fees_mult
    if move_bps < min_edge:
        return False, f"expected edge {move_bps:.1f}bps < min vs fees {min_edge:.1f}bps"

    return True, "ok"
