from abc import ABC, abstractmethod
from typing import TypedDict, List, Optional, Deque
from dataclasses import dataclass
import time
from collections import deque

class NormalizedTick(TypedDict):
    symbol: str
    price: float
    qty: float
    side: str
    timestamp_ms: int
    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float

@dataclass
class Signal:
    symbol: str
    direction: str # "BUY" or "SELL"
    strength: float # 0.0 to 1.0
    strategy_name: str
    timestamp_ms: int
    suggested_price: float
    suggested_qty: Optional[float] = None

@dataclass
class MarketContext:
    price_history: Deque[float]
    tick_history: Deque[NormalizedTick]
    current_position: Optional[dict]

class BaseStrategy(ABC):
    def __init__(self, params: dict = None):
        self.name = self.__class__.__name__
        self.params = params or {}
        self.weight = self.params.get("weight", 1.0)
        self.enabled = self.params.get("enabled", True)

    @abstractmethod
    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        pass

class EMAStrategy(BaseStrategy):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.fast_period = self.params.get("fast_period", 5)
        self.slow_period = self.params.get("slow_period", 20)

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        if len(context.price_history) < self.slow_period:
            return None

        prices = list(context.price_history)[-self.slow_period:]
        fast_ema = sum(prices[-self.fast_period:]) / self.fast_period
        slow_ema = sum(prices) / self.slow_period

        if fast_ema > slow_ema * 1.0001:
            return Signal(
                symbol=tick["symbol"],
                direction="BUY",
                strength=1.0,
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"]
            )
        elif fast_ema < slow_ema * 0.9999:
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=1.0,
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"]
            )
        return None

class OrderBookImbalanceStrategy(BaseStrategy):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.imbalance_threshold = self.params.get("imbalance_threshold", 0.6)

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
                suggested_price=tick["ask_price"] if tick["ask_price"] > 0 else tick["price"]
            )
        elif ratio < (1 - self.imbalance_threshold):
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=1.0 - ratio,
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["bid_price"] if tick["bid_price"] > 0 else tick["price"]
            )
        return None

class MomentumBurstStrategy(BaseStrategy):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.threshold_pct = self.params.get("threshold_pct", 0.001) # 0.1%
        self.window_ms = self.params.get("window_ms", 10000) # 10 seconds

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Optional[Signal]:
        if len(context.tick_history) < 2:
            return None
            
        current_time = tick["timestamp_ms"]
        # Find oldest tick within the window
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
                strength=min(1.0, pct_change / self.threshold_pct),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"]
            )
        elif pct_change < -self.threshold_pct:
            return Signal(
                symbol=tick["symbol"],
                direction="SELL",
                strength=min(1.0, abs(pct_change) / self.threshold_pct),
                strategy_name=self.name,
                timestamp_ms=tick["timestamp_ms"],
                suggested_price=tick["price"]
            )
        return None
