from abc import ABC, abstractmethod
import time
from models import NormalizedTick, Signal, MarketContext

class BaseStrategy(ABC):
    def __init__(self, name: str, is_shadow: bool = False):
        self.name = name
        self.is_shadow = is_shadow

    @abstractmethod
    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Signal | None:
        pass

class EMAStrategy(BaseStrategy):
    def __init__(self, is_shadow: bool = False):
        super().__init__("EMA Crossover", is_shadow)

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Signal | None:
        if tick.type != "trade" or not tick.price:
            return None

        history = context.price_history.get(tick.symbol, [])
        if len(history) < 20:
            return None

        # Simple SMA for scaffold demonstration
        avg = sum(history[-20:]) / 20
        if tick.price > avg * 1.01:
            return Signal(tick.symbol, "BUY", 0.8, self.name, int(time.time()*1000), tick.price, 0.01, self.is_shadow)
        elif tick.price < avg * 0.99:
            return Signal(tick.symbol, "SELL", 0.8, self.name, int(time.time()*1000), tick.price, 0.01, self.is_shadow)
        return None

class OrderBookImbalanceStrategy(BaseStrategy):
    def __init__(self, is_shadow: bool = False):
        super().__init__("OrderBook Imbalance", is_shadow)

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Signal | None:
        if tick.type != "bookTicker" or not tick.bid_qty or not tick.ask_qty:
            return None

        total_vol = tick.bid_qty + tick.ask_qty
        if total_vol == 0:
            return None

        ratio = tick.bid_qty / total_vol
        if ratio > 0.8:
            return Signal(tick.symbol, "BUY", ratio, self.name, int(time.time()*1000), tick.bid_price or 0, 0.01, self.is_shadow)
        elif ratio < 0.2:
            return Signal(tick.symbol, "SELL", 1-ratio, self.name, int(time.time()*1000), tick.ask_price or 0, 0.01, self.is_shadow)
        return None

class MomentumBurstStrategy(BaseStrategy):
    def __init__(self, is_shadow: bool = False, lookback: int = 5, threshold: float = 0.005):
        super().__init__("Momentum Burst", is_shadow)
        self.lookback = lookback
        self.threshold = threshold

    def generate_signal(self, tick: NormalizedTick, context: MarketContext) -> Signal | None:
        if tick.type != "trade" or not tick.price:
            return None

        history = context.price_history.get(tick.symbol, [])
        if len(history) < self.lookback:
            return None

        old_price = history[-self.lookback]
        current_price = tick.price

        # Calculate rate of change over the lookback period
        roc = (current_price - old_price) / old_price

        # If rate of change exceeds threshold, ride the momentum
        if roc > self.threshold:
             return Signal(tick.symbol, "BUY", min(1.0, roc / self.threshold * 0.5), self.name, int(time.time()*1000), tick.price, 0.01, self.is_shadow)
        elif roc < -self.threshold:
             return Signal(tick.symbol, "SELL", min(1.0, abs(roc) / self.threshold * 0.5), self.name, int(time.time()*1000), tick.price, 0.01, self.is_shadow)

        return None
