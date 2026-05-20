"""Technical indicators for tick-level scalping (Decimal-safe, no lookahead)."""
from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Deque, Optional, Sequence

D = Decimal
ZERO = D("0")
ONE = D("1")
TWO = D("2")


def ema_series(prices: Sequence[D], period: int) -> Optional[D]:
    if len(prices) < period:
        return None
    multiplier = TWO / D(str(period + 1))
    value = sum(prices[:period]) / D(str(period))
    for price in prices[period:]:
        value = (price - value) * multiplier + value
    return value


def ema_update(price: D, period: int, prev: Optional[D]) -> Optional[D]:
    if prev is None:
        return price
    multiplier = TWO / D(str(period + 1))
    return (price - prev) * multiplier + prev


def true_range(high: D, low: D, prev_close: D) -> D:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr_from_ticks(ticks: Deque, period: int = 14) -> D:
    """Blend tick TR average with recent range — tick-only TR understates vol for filters."""
    if len(ticks) < 2:
        return ZERO
    prices = [t["price"] for t in ticks]
    trs: list[D] = []
    prev_close = prices[0]
    for price in prices[1:]:
        trs.append(abs(price - prev_close))
        prev_close = price
    tr_window = trs[-period:] if len(trs) >= period else trs
    tr_avg = sum(tr_window) / D(str(len(tr_window))) if tr_window else ZERO

    recent = prices[-period:] if len(prices) >= period else prices
    range_atr = (max(recent) - min(recent)) / TWO if recent else ZERO

    return max(tr_avg, range_atr)


def spread_bps(bid: D, ask: D) -> D:
    if bid <= ZERO or ask <= ZERO or ask <= bid:
        return D("9999")
    mid = (bid + ask) / TWO
    return ((ask - bid) / mid) * D("10000")


def vwap(ticks: Deque) -> Optional[D]:
    vol = ZERO
    pv = ZERO
    for t in ticks:
        vol += t["qty"]
        pv += t["price"] * t["qty"]
    if vol <= ZERO:
        return None
    return pv / vol


def rsi(prices: Sequence[D], period: int = 14) -> Optional[D]:
    if len(prices) < period + 1:
        return None
    gains = ZERO
    losses = ZERO
    for i in range(-period, 0):
        delta = prices[i] - prices[i - 1]
        if delta > ZERO:
            gains += delta
        else:
            losses += abs(delta)
    if losses == ZERO:
        return D("100")
    rs = gains / losses
    return D("100") - (D("100") / (ONE + rs))


def bollinger(prices: Sequence[D], period: int = 20, std_mult: D = D("2")) -> tuple[Optional[D], Optional[D], Optional[D]]:
    if len(prices) < period:
        return None, None, None
    window = list(prices)[-period:]
    mid = sum(window) / D(str(period))
    variance = sum((p - mid) ** 2 for p in window) / D(str(period))
    # integer sqrt approximation via pow for small windows
    std = variance.sqrt() if hasattr(variance, "sqrt") else D(str(float(variance) ** 0.5))
    return mid - std_mult * std, mid, mid + std_mult * std


def price_change_pct(ticks: Deque, window_ms: int, now_ms: int) -> Optional[D]:
    if len(ticks) < 2:
        return None
    ref = None
    for t in ticks:
        if now_ms - t["timestamp_ms"] <= window_ms:
            ref = t
            break
    if ref is None or ref["price"] <= ZERO:
        return None
    last = ticks[-1]["price"]
    return (last - ref["price"]) / ref["price"]


def volume_sum(ticks: Deque, window_ms: int, now_ms: int) -> D:
    total = ZERO
    for t in ticks:
        if now_ms - t["timestamp_ms"] <= window_ms:
            total += t["qty"]
    return total


def chop_ratio(prices: Sequence[D], atr: D, window: int = 30) -> D:
    """Range/ATR over a short window — not full history (avoids permanent filter block)."""
    if len(prices) < 5 or atr <= ZERO:
        return D("999")
    recent = list(prices)[-window:] if len(prices) > window else list(prices)
    hi = max(recent)
    lo = min(recent)
    return (hi - lo) / atr
