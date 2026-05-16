from abc import ABC, abstractmethod
from typing import TypedDict, List, Optional, Deque
from dataclasses import dataclass
import time
from collections import deque
from decimal import Decimal, getcontext

getcontext().prec = 28

class NormalizedTick(TypedDict):
    symbol: str
    price: Decimal
    qty: Decimal
    side: str
    timestamp_ms: int
    bid_price: Decimal
    ask_price: Decimal
    bid_qty: Decimal
    ask_qty: Decimal

@dataclass
class Signal:
    symbol: str
    direction: str # "BUY" or "SELL"
    strength: Decimal # 0.0 to 1.0
    strategy_name: str
    timestamp_ms: int
    suggested_price: Decimal
    suggested_qty: Optional[Decimal] = None
    ab_variant: str = "A"

@dataclass
class MarketContext:
    price_history: Deque[Decimal]
    tick_history: Deque[NormalizedTick]
    current_position: Optional[dict]

class BaseStrategy(ABC):
    def __init__(self, params: dict = None):
        self.name = self.__class__.__name__
        self.params = params or {}
        self.weight = Decimal(str(self.params.get("weight", "1.0")))
        self.enabled = self.params.get("enabled", True)
        self.ab_variant = self.params.get("ab_variant", "A")

    @abstractmethod
    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        pass

class EMAStrategy(BaseStrategy):
    """
    Nota: Implementazione corretta di EMA (Exponential Moving Average).
    Formula: EMA = (Price - Previous EMA) * Multiplier + Previous EMA
    """
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.fast_period = self.params.get("fast_period", 5)
        self.slow_period = self.params.get("slow_period", 20)
        self.prev_fast_ema = None
        self.prev_slow_ema = None

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        if len(context.price_history) < self.slow_period:
            return None

        prices = list(context.price_history)
        
        # Calcolo EMA corretto
        def calc_ema(prices, period, prev_ema):
            multiplier = Decimal("2") / Decimal(str(period + 1))
            if prev_ema is None:
                # Semplice SMA per il primo valore
                return sum(prices[:period]) / Decimal(str(period))
            return (prices[-1] - prev_ema) * multiplier + prev_ema

        fast_ema = calc_ema(prices, self.fast_period, self.prev_fast_ema)
        slow_ema = calc_ema(prices, self.slow_period, self.prev_slow_ema)
        
        self.prev_fast_ema = fast_ema
        self.prev_slow_ema = slow_ema

        if fast_ema > slow_ema * Decimal("1.001"):
            return Signal(
                symbol=tick["symbol"],
                direction="BUY",
                strength=Decimal("1.0"),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"],
                ab_variant=self.ab_variant
            )
        elif fast_ema < slow_ema * Decimal("0.999"):
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=Decimal("1.0"),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"],
                ab_variant=self.ab_variant
            )
        return None

class OrderBookImbalanceStrategy(BaseStrategy):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.imbalance_threshold = Decimal(str(self.params.get("imbalance_threshold", "0.7")))

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        bid_qty = tick["bid_qty"]
        ask_qty = tick["ask_qty"]
        total_qty = bid_qty + ask_qty

        if total_qty == 0:
            return None

        ratio = bid_qty / total_qty
        
        if ratio > self.imbalance_threshold:
            return Signal(
                symbol=tick["symbol"],
                direction="BUY",
                strength=ratio,
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["ask_price"] if tick["ask_price"] > 0 else tick["price"],
                ab_variant=self.ab_variant
            )
        elif ratio < (Decimal("1") - self.imbalance_threshold):
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=Decimal("1") - ratio,
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["bid_price"] if tick["bid_price"] > 0 else tick["price"],
                ab_variant=self.ab_variant
            )
        return None

class MomentumBurstStrategy(BaseStrategy):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.threshold_pct = Decimal(str(self.params.get("threshold_pct", "0.005")))
        self.window_ms = self.params.get("window_ms", 10000)

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        if len(context.tick_history) < 2:
            return None
            
        current_time = tick["timestamp_ms"]
        oldest_tick = None
        for t in context.tick_history:
            if current_time - t["timestamp_ms"] <= self.window_ms:
                oldest_tick = t
                break
                
        if not oldest_tick:
            return None
            
        old_price = oldest_tick["price"]
        if old_price == 0:
            return None
            
        pct_change = (tick["price"] - old_price) / old_price
        
        if pct_change > self.threshold_pct:
            return Signal(
                symbol=tick["symbol"],
                direction="BUY",
                strength=min(Decimal("1.0"), pct_change / self.threshold_pct),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"],
                ab_variant=self.ab_variant
            )
        elif pct_change < -self.threshold_pct:
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=min(Decimal("1.0"), abs(pct_change) / self.threshold_pct),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"],
                ab_variant=self.ab_variant
            )
        return None

class VWAPDeviationStrategy(BaseStrategy):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.deviation_threshold = Decimal(str(self.params.get("deviation_threshold", "0.005")))
        self.min_ticks = self.params.get("min_ticks", 10)

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        if len(context.tick_history) < self.min_ticks:
            return None
            
        total_volume = Decimal("0")
        total_pv = Decimal("0")
        
        for t in context.tick_history:
            total_volume += t["qty"]
            total_pv += t["price"] * t["qty"]
            
        if total_volume == 0:
            return None
            
        vwap = total_pv / total_volume
        deviation = (tick["price"] - vwap) / vwap
        
        if deviation < -self.deviation_threshold:
            # Sottovalutato rispetto al VWAP -> BUY
            return Signal(
                symbol=tick["symbol"],
                direction="BUY",
                strength=min(Decimal("1.0"), abs(deviation) / self.deviation_threshold),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"],
                ab_variant=self.ab_variant
            )
        elif deviation > self.deviation_threshold:
            # Sopravalutato rispetto al VWAP -> SELL
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=min(Decimal("1.0"), deviation / self.deviation_threshold),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"],
                ab_variant=self.ab_variant
            )
        return None

