import pytest
import copy
from signal_engine.models import NormalizedTick, Signal, MarketContext
from signal_engine.optimizer import run_backtest

class MockStrategy:
    def __init__(self):
        self.contexts = []
        self.signals_to_return = []

    def generate_signal(self, tick, context):
        self.contexts.append(copy.deepcopy(context.price_history))
        if self.signals_to_return:
            return self.signals_to_return.pop(0)
        return None

def test_run_backtest_empty_ticks():
    result = run_backtest([], MockStrategy(), history_size=10)
    assert result == {
        "signals_generated": 0,
        "estimated_win_rate": 0,
        "estimated_pnl": 0.0
    }

def test_run_backtest_with_signals():
    strategy = MockStrategy()
    strategy.signals_to_return = [
        Signal("BTCUSDT", "BUY", 1.0, "Mock", 1000, 50000, 1.0),
        None,
        Signal("BTCUSDT", "SELL", 1.0, "Mock", 1000, 50000, 1.0)
    ]
    ticks = [
        NormalizedTick("BTCUSDT", 1000, "trade", price=100),
        NormalizedTick("BTCUSDT", 1001, "trade", price=101),
        NormalizedTick("BTCUSDT", 1002, "trade", price=102)
    ]

    result = run_backtest(ticks, strategy, history_size=10)

    assert result["signals_generated"] == 2
    assert result["estimated_win_rate"] == 2 * 0.52
    assert result["estimated_pnl"] == 2 * 0.15

    assert strategy.contexts[0] == {"BTCUSDT": [100]}
    assert strategy.contexts[1] == {"BTCUSDT": [100, 101]}
    assert strategy.contexts[2] == {"BTCUSDT": [100, 101, 102]}

def test_run_backtest_history_bounding():
    strategy = MockStrategy()
    ticks = [
        NormalizedTick("BTCUSDT", 1000, "trade", price=100),
        NormalizedTick("BTCUSDT", 1001, "trade", price=101),
        NormalizedTick("BTCUSDT", 1002, "trade", price=102),
        NormalizedTick("BTCUSDT", 1003, "trade", price=103),
    ]

    run_backtest(ticks, strategy, history_size=2)

    assert strategy.contexts[0] == {"BTCUSDT": [100]}
    assert strategy.contexts[1] == {"BTCUSDT": [100, 101]}
    assert strategy.contexts[2] == {"BTCUSDT": [101, 102]}
    assert strategy.contexts[3] == {"BTCUSDT": [102, 103]}

def test_run_backtest_non_trade_ticks():
    strategy = MockStrategy()
    ticks = [
        NormalizedTick("BTCUSDT", 1000, "trade", price=100),
        NormalizedTick("BTCUSDT", 1001, "bookTicker", bid_price=99, ask_price=101),
        NormalizedTick("BTCUSDT", 1002, "trade", price=102),
    ]

    run_backtest(ticks, strategy, history_size=2)

    assert strategy.contexts[0] == {"BTCUSDT": [100]}
    assert strategy.contexts[1] == {"BTCUSDT": [100]}
    assert strategy.contexts[2] == {"BTCUSDT": [100, 102]}

def test_run_backtest_trade_tick_without_price():
    strategy = MockStrategy()
    ticks = [
        NormalizedTick("BTCUSDT", 1000, "trade", price=100),
        NormalizedTick("BTCUSDT", 1001, "trade", price=None),
        NormalizedTick("BTCUSDT", 1002, "trade", price=102),
    ]

    run_backtest(ticks, strategy, history_size=2)

    assert strategy.contexts[0] == {"BTCUSDT": [100]}
    assert strategy.contexts[1] == {"BTCUSDT": [100]}
    assert strategy.contexts[2] == {"BTCUSDT": [100, 102]}
