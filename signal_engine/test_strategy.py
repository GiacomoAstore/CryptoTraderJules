import pytest
from signal_engine.models import NormalizedTick, MarketContext
from signal_engine.strategy import MomentumBurstStrategy

def test_momentum_burst_strategy_no_signal_with_insufficient_history():
    strategy = MomentumBurstStrategy(lookback=5, threshold=0.01)
    context = MarketContext(price_history={"BTCUSDT": [100, 101, 102]})
    tick = NormalizedTick("BTCUSDT", 1000, "trade", price=103)

    signal = strategy.generate_signal(tick, context)
    assert signal is None

def test_momentum_burst_strategy_buy_signal():
    strategy = MomentumBurstStrategy(lookback=3, threshold=0.05)
    # Old price (history[-3]) will be 100. Current price 110. ROC = 10%
    context = MarketContext(price_history={"BTCUSDT": [100, 101, 102]})
    tick = NormalizedTick("BTCUSDT", 1000, "trade", price=110)

    signal = strategy.generate_signal(tick, context)
    assert signal is not None
    assert signal.direction == "BUY"
    assert signal.symbol == "BTCUSDT"

def test_momentum_burst_strategy_sell_signal():
    strategy = MomentumBurstStrategy(lookback=3, threshold=0.05)
    # Old price (history[-3]) will be 100. Current price 90. ROC = -10%
    context = MarketContext(price_history={"BTCUSDT": [100, 99, 98]})
    tick = NormalizedTick("BTCUSDT", 1000, "trade", price=90)

    signal = strategy.generate_signal(tick, context)
    assert signal is not None
    assert signal.direction == "SELL"
    assert signal.symbol == "BTCUSDT"

def test_momentum_burst_strategy_invalid_tick_type():
    strategy = MomentumBurstStrategy(lookback=3, threshold=0.05)
    context = MarketContext(price_history={"BTCUSDT": [100, 101, 102]})
    tick = NormalizedTick("BTCUSDT", 1000, "bookTicker", price=110)

    signal = strategy.generate_signal(tick, context)
    assert signal is None

def test_momentum_burst_strategy_missing_price():
    strategy = MomentumBurstStrategy(lookback=3, threshold=0.05)
    context = MarketContext(price_history={"BTCUSDT": [100, 101, 102]})
    tick = NormalizedTick("BTCUSDT", 1000, "trade", price=None)

    signal = strategy.generate_signal(tick, context)
    assert signal is None

def test_momentum_burst_strategy_below_threshold():
    strategy = MomentumBurstStrategy(lookback=3, threshold=0.05)
    context = MarketContext(price_history={"BTCUSDT": [100, 101, 102]})
    tick = NormalizedTick("BTCUSDT", 1000, "trade", price=101)

    signal = strategy.generate_signal(tick, context)
    assert signal is None

def test_momentum_burst_strategy_zero_old_price():
    strategy = MomentumBurstStrategy(lookback=3, threshold=0.05)
    context = MarketContext(price_history={"BTCUSDT": [0, 101, 102]})
    tick = NormalizedTick("BTCUSDT", 1000, "trade", price=110)

    signal = strategy.generate_signal(tick, context)
    assert signal is None
