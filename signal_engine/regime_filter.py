"""Live EMA trend regime filter (fast/slow), independent of crossover signal state."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

from indicators import ema_update

D = Decimal


class EmaTrendRegime:
    """Per-symbol EMA state for momentum_ema_strict aggregation."""

    def __init__(self, fast_period: int, slow_period: int, min_separation_bps: D):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.min_separation_bps = min_separation_bps
        self._prev_fast_ema: Optional[D] = None
        self._prev_slow_ema: Optional[D] = None

    def aligned(self, price: D, direction: str) -> Tuple[bool, D]:
        if price <= D("0"):
            return False, D("0")

        fast = ema_update(price, self.fast_period, self._prev_fast_ema)
        slow = ema_update(price, self.slow_period, self._prev_slow_ema)
        if fast is None or slow is None:
            return False, D("0")

        self._prev_fast_ema = fast
        self._prev_slow_ema = slow
        sep_bps = abs(fast - slow) / price * D("10000")
        if sep_bps < self.min_separation_bps:
            return False, sep_bps
        if direction == "BUY" and fast > slow:
            return True, sep_bps
        if direction == "SELL" and fast < slow:
            return True, sep_bps
        return False, sep_bps
