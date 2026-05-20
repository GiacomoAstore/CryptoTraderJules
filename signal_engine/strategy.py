"""
Scalping strategies — tick-level, fee-aware, filtered for live crypto HFT.
Exits (SL/TP/trailing) are enforced by risk_manager + order_executor.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Deque, Optional

from decimal import getcontext

from indicators import (
    bollinger,
    chop_ratio,
    ema_update,
    price_change_pct,
    rsi,
    spread_bps,
    vwap,
    volume_sum,
)

getcontext().prec = 28

D = Decimal
ZERO = D("0")
ONE = D("1")


class NormalizedTick(dict):
    """TypedDict-compatible tick."""


@dataclass
class Signal:
    symbol: str
    direction: str
    strength: Decimal
    strategy_name: str
    timestamp_ms: int
    suggested_price: Decimal
    suggested_qty: Optional[Decimal] = None
    ab_variant: str = "A"
    expected_edge_bps: Decimal = D("15")


@dataclass
class MarketContext:
    price_history: Deque[Decimal]
    tick_history: Deque
    current_position: Optional[dict]
    atr: Decimal = ZERO
    atr_pct: Decimal = ZERO
    spread_bps: Decimal = ZERO


class BaseStrategy(ABC):
    def __init__(self, params: dict | None = None):
        self.params = params or {}
        self.name = self.__class__.__name__
        self.weight = D(str(self.params.get("weight", "1.0")))
        self.enabled = self.params.get("enabled", True)
        self.ab_variant = self.params.get("ab_variant", "A")
        self.min_book_qty = D(str(self.params.get("min_book_qty", "0")))

    def _book_ok(self, tick: dict) -> bool:
        if tick["bid_price"] <= ZERO or tick["ask_price"] <= ZERO:
            return False
        if self.min_book_qty > ZERO:
            if tick["bid_qty"] < self.min_book_qty or tick["ask_qty"] < self.min_book_qty:
                return False
        return True

    def _max_spread_bps(self, tick: dict) -> bool:
        cap = D(str(self.params.get("max_spread_bps", "12")))
        spr = spread_bps(tick["bid_price"], tick["ask_price"])
        return spr <= cap

    def _edge_bps(self, context: MarketContext, raw_edge: D) -> D:
        """Fee-aware edge floor (~3.5x ATR move) so global filters are not trivially blocking."""
        atr_floor = context.atr_pct * D("3500") if context.atr_pct > ZERO else D("40")
        return max(raw_edge, atr_floor, D("40"))

    def _trending_market(self, context: MarketContext, max_chop: D = D("4.5")) -> bool:
        if context.atr <= ZERO or len(context.price_history) < 10:
            return True
        chop = chop_ratio(list(context.price_history), context.atr, window=25)
        return chop > max_chop

    @abstractmethod
    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        pass

    def _signal(
        self,
        tick: dict,
        direction: str,
        strength: Decimal,
        price: Decimal | None = None,
        edge_bps: Decimal = D("15"),
    ) -> Signal:
        strength = max(D("0.1"), min(ONE, strength))
        return Signal(
            symbol=tick["symbol"],
            direction=direction,
            strength=strength,
            strategy_name=self.name,
            timestamp_ms=tick["timestamp_ms"],
            suggested_price=price or tick["price"],
            ab_variant=self.ab_variant,
            expected_edge_bps=edge_bps,
        )


class EMACrossoverStrategy(BaseStrategy):
    """Micro trend: EMA crossover only (no persistent state bias / overtrading)."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.fast_period = int(self.params.get("fast_period", 8))
        self.slow_period = int(self.params.get("slow_period", 21))
        self.min_separation_bps = D(str(self.params.get("min_separation_bps", "3")))
        self._prev_fast: Optional[D] = None
        self._prev_slow: Optional[D] = None
        self._prev_fast_ema: Optional[D] = None
        self._prev_slow_ema: Optional[D] = None

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        if len(context.price_history) < self.slow_period:
            return None
        if tick["price"] <= ZERO or not self._book_ok(tick) or not self._max_spread_bps(tick):
            return None

        price = tick["price"]
        fast = ema_update(price, self.fast_period, self._prev_fast_ema)
        slow = ema_update(price, self.slow_period, self._prev_slow_ema)
        if fast is None or slow is None:
            return None

        sep_bps = abs(fast - slow) / price * D("10000")
        if sep_bps < self.min_separation_bps:
            self._prev_fast_ema, self._prev_slow_ema = fast, slow
            return None

        edge_bps = self._edge_bps(context, sep_bps)
        signal = None
        if self._prev_fast is not None and self._prev_slow is not None:
            if self._prev_fast <= self._prev_slow and fast > slow:
                signal = self._signal(tick, "BUY", sep_bps / D("20"), tick["ask_price"], edge_bps)
            elif self._prev_fast >= self._prev_slow and fast < slow:
                signal = self._signal(tick, "SELL", sep_bps / D("20"), tick["bid_price"], edge_bps)

        self._prev_fast, self._prev_slow = fast, slow
        self._prev_fast_ema, self._prev_slow_ema = fast, slow
        return signal


# Backward-compatible alias
EMAStrategy = EMACrossoverStrategy


class OrderBookImbalanceStrategy(BaseStrategy):
    """Order flow: rising book imbalance + trade aggression (not static book bias)."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.imbalance_threshold = D(str(self.params.get("imbalance_threshold", "0.62")))
        self.min_trade_qty = D(str(self.params.get("min_trade_qty", "0")))
        self.min_ratio_delta = D(str(self.params.get("min_ratio_delta", "0.04")))
        self._prev_ratio: Optional[D] = None

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        if not self._book_ok(tick) or not self._max_spread_bps(tick):
            return None

        bid_qty = tick["bid_qty"]
        ask_qty = tick["ask_qty"]
        total = bid_qty + ask_qty
        if total <= ZERO:
            return None

        ratio = bid_qty / total
        trade_qty = tick["qty"]
        if trade_qty < self.min_trade_qty:
            self._prev_ratio = ratio
            return None

        prev = self._prev_ratio
        self._prev_ratio = ratio
        if prev is None:
            return None

        delta = ratio - prev
        spr = spread_bps(tick["bid_price"], tick["ask_price"])
        edge = self._edge_bps(context, (abs(ratio - D("0.5")) * D("200")) + spr)

        if (
            ratio >= self.imbalance_threshold
            and delta >= self.min_ratio_delta
            and tick["side"] == "BUY"
        ):
            return self._signal(tick, "BUY", min(ONE, ratio), tick["ask_price"], edge)
        if (
            ratio <= (ONE - self.imbalance_threshold)
            and delta <= -self.min_ratio_delta
            and tick["side"] == "SELL"
        ):
            return self._signal(tick, "SELL", min(ONE, ONE - ratio), tick["bid_price"], edge)
        return None


class MomentumBurstStrategy(BaseStrategy):
    """Volatility-normalized momentum burst (ATR-scaled threshold)."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.atr_mult = D(str(self.params.get("atr_mult", "1.2")))
        self.window_ms = int(self.params.get("window_ms", 4000))
        self.min_volume = D(str(self.params.get("min_volume", "0.1")))

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        if context.atr <= ZERO or tick["price"] <= ZERO:
            return None
        if not self._max_spread_bps(tick):
            return None

        vol = volume_sum(context.tick_history, self.window_ms, tick["timestamp_ms"])
        if vol < self.min_volume:
            return None

        pct = price_change_pct(context.tick_history, self.window_ms, tick["timestamp_ms"])
        if pct is None:
            return None

        threshold = (context.atr / tick["price"]) * self.atr_mult
        if threshold <= ZERO:
            return None
        edge_bps = self._edge_bps(context, abs(pct) * D("10000"))

        if pct > threshold and tick["side"] == "BUY":
            strength = min(ONE, pct / threshold)
            return self._signal(tick, "BUY", strength, tick["ask_price"], edge_bps)
        if pct < -threshold and tick["side"] == "SELL":
            strength = min(ONE, abs(pct) / threshold)
            return self._signal(tick, "SELL", strength, tick["bid_price"], edge_bps)
        return None


class VWAPDeviationStrategy(BaseStrategy):
    """Mean reversion to session VWAP with RSI guard (avoid falling knives)."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.deviation_threshold = D(str(self.params.get("deviation_threshold", "0.004")))
        self.min_ticks = int(self.params.get("min_ticks", 15))
        self.rsi_oversold = D(str(self.params.get("rsi_oversold", "35")))
        self.rsi_overbought = D(str(self.params.get("rsi_overbought", "65")))

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        if len(context.tick_history) < self.min_ticks:
            return None
        if not self._book_ok(tick) or not self._max_spread_bps(tick):
            return None
        if self._trending_market(context):
            return None

        vw = vwap(context.tick_history)
        if vw is None or vw <= ZERO:
            return None

        deviation = (tick["price"] - vw) / vw
        prices = list(context.price_history)
        r = rsi(prices, 14)
        if r is None:
            return None

        edge_bps = self._edge_bps(context, abs(deviation) * D("10000"))

        if deviation < -self.deviation_threshold and r < self.rsi_oversold:
            return self._signal(
                tick, "BUY", min(ONE, abs(deviation) / self.deviation_threshold),
                tick["ask_price"], edge_bps,
            )
        if deviation > self.deviation_threshold and r > self.rsi_overbought:
            return self._signal(
                tick, "SELL", min(ONE, deviation / self.deviation_threshold),
                tick["bid_price"], edge_bps,
            )
        return None


class VolatilityExpansionStrategy(BaseStrategy):
    """Squeeze breakout: low chop then expansion beyond ATR band."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.squeeze_chop_max = D(str(self.params.get("squeeze_chop_max", "2.5")))
        self.breakout_atr_mult = D(str(self.params.get("breakout_atr_mult", "1.5")))

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        if len(context.price_history) < 20 or context.atr <= ZERO:
            return None
        if not self._max_spread_bps(tick):
            return None

        prices = list(context.price_history)
        recent = prices[-10:]
        range_recent = max(recent) - min(recent)
        chop = range_recent / context.atr
        if chop > self.squeeze_chop_max:
            return None

        ref = prices[-5]
        move = tick["price"] - ref
        threshold = context.atr * self.breakout_atr_mult
        if tick["price"] <= ZERO or threshold <= ZERO:
            return None
        edge_bps = self._edge_bps(context, abs(move / tick["price"]) * D("10000"))

        if move > threshold and tick["side"] == "BUY":
            return self._signal(tick, "BUY", min(ONE, move / threshold), tick["ask_price"], edge_bps)
        if move < -threshold and tick["side"] == "SELL":
            return self._signal(tick, "SELL", min(ONE, abs(move) / threshold), tick["bid_price"], edge_bps)
        return None


class BollingerMeanReversionStrategy(BaseStrategy):
    """Bollinger band touch + RSI confirmation."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.period = int(self.params.get("period", 20))
        self.std_mult = D(str(self.params.get("std_mult", "2.0")))

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        prices = list(context.price_history)
        if len(prices) < self.period:
            return None
        if not self._book_ok(tick) or not self._max_spread_bps(tick):
            return None
        if self._trending_market(context):
            return None

        lower, mid, upper = bollinger(prices, self.period, self.std_mult)
        if lower is None or upper is None:
            return None

        r = rsi(prices, 14)
        if r is None:
            return None

        price = tick["price"]
        if price <= lower and r < D("40"):
            raw = (mid - price) / price * D("10000") if mid else D("15")
            return self._signal(tick, "BUY", D("0.8"), tick["ask_price"], self._edge_bps(context, raw))
        if price >= upper and r > D("60"):
            raw = (price - mid) / price * D("10000") if mid else D("15")
            return self._signal(tick, "SELL", D("0.8"), tick["bid_price"], self._edge_bps(context, raw))
        return None


class MicroStructureBreakoutStrategy(BaseStrategy):
    """Liquidity sweep proxy: pierce recent micro high/low then reject."""

    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.lookback = int(self.params.get("lookback", 25))
        self.pierce_bps = D(str(self.params.get("pierce_bps", "2")))

    def generate_signal(self, tick: dict, context: MarketContext) -> Optional[Signal]:
        prices = list(context.price_history)
        if len(prices) < self.lookback + 2:
            return None
        if not self._book_ok(tick) or not self._max_spread_bps(tick):
            return None

        window = prices[-(self.lookback + 1):-1]
        hi = max(window)
        lo = min(window)
        price = tick["price"]
        pierce = self.pierce_bps / D("10000")

        if price > hi * (ONE + pierce) and tick["side"] == "SELL":
            return self._signal(
                tick, "SELL", D("0.7"), tick["bid_price"],
                self._edge_bps(context, self.pierce_bps * D("2")),
            )
        if price < lo * (ONE - pierce) and tick["side"] == "BUY":
            return self._signal(
                tick, "BUY", D("0.7"), tick["ask_price"],
                self._edge_bps(context, self.pierce_bps * D("2")),
            )
        return None
