from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from decimal import Decimal
from typing import Any

import redis.asyncio as redis

try:
    from candles import CandleState
except ModuleNotFoundError:
    from signal_engine.candles import CandleState

logger = logging.getLogger("Breakout1m")

DEFAULT_LOOKBACK = 20
DEFAULT_VOLUME_MA_MULTIPLIER = Decimal("1.0")
DEFAULT_BREAKOUT_BUFFER_BPS = Decimal("0")


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values) / Decimal(len(values))


class Breakout1mEngine:
    def __init__(self, redis_client: redis.Redis, config: dict[str, Any] | None = None):
        self.redis_client = redis_client
        self.config = config or {}
        self.breakout_lookback = int(self.config.get("breakout_lookback", DEFAULT_LOOKBACK))
        self.volume_ma_multiplier = Decimal(str(self.config.get("volume_ma_multiplier", DEFAULT_VOLUME_MA_MULTIPLIER)))
        self.breakout_buffer_bps = Decimal(str(self.config.get("breakout_buffer_bps", DEFAULT_BREAKOUT_BUFFER_BPS)))
        self.enabled = bool(self.config.get("breakout_enabled", True))
        self.candle_state: dict[str, CandleState] = {}
        self.candle_history: dict[str, deque[CandleState]] = defaultdict(lambda: deque(maxlen=self.breakout_lookback))

    async def initialize(self) -> None:
        active_keys = await self.redis_client.keys("signal_engine:1m_candle:*")
        for key in active_keys:
            payload = await self.redis_client.get(key)
            if payload:
                candle = CandleState.from_json(payload)
                self.candle_state[candle.symbol] = candle

        history_keys = await self.redis_client.keys("signal_engine:1m_candle_history:*")
        for key in history_keys:
            symbol = key.split(":", 3)[-1]
            entries = await self.redis_client.lrange(key, 0, self.breakout_lookback - 1)
            for entry in reversed(entries):
                self.candle_history[symbol].append(CandleState.from_json(entry))
        logger.info("Breakout1m engine initialized | symbols_loaded=%d", len(self.candle_state))

    async def process_tick(self, tick: dict[str, Any]) -> None:
        if not self.enabled:
            return

        symbol = tick.get("symbol")
        if not symbol:
            return

        timestamp_ms = int(tick["timestamp_ms"])
        current = self.candle_state.get(symbol)

        if current and not current.is_same_minute(timestamp_ms):
            await self._close_candle(symbol)
            current = None

        if current is None:
            current = CandleState.from_tick(tick)
            self.candle_state[symbol] = current
        else:
            current.update(tick)

        await self._persist_active_candle(symbol)

    async def _close_candle(self, symbol: str) -> None:
        candle = self.candle_state.pop(symbol, None)
        if candle is None:
            return
        logger.info("Breakout1m candle closed [%s] | history=%d", symbol, len(self.candle_history[symbol]))

        prior_candles = list(self.candle_history[symbol])
        signal = self._build_breakout_signal(candle, prior_candles)
        self.candle_history[symbol].append(candle)
        await self._persist_history(symbol, candle)
        await self._delete_active_candle(symbol)

        if signal:
            await self._publish_signal(symbol, signal)

    def _build_breakout_signal(self, candle: CandleState, prior_candles: list[CandleState]) -> dict[str, Any] | None:
        if not prior_candles:
            return None

        lookback_candles = prior_candles[-self.breakout_lookback :]
        if not lookback_candles:
            return None

        highs = [c.high for c in lookback_candles]
        lows = [c.low for c in lookback_candles]
        volumes = [c.volume for c in lookback_candles]

        if not highs or not lows or not volumes:
            return None

        previous_high = max(highs)
        previous_low = min(lows)
        volume_ma = _mean(volumes)
        if volume_ma <= 0:
            return None

        if candle.volume <= volume_ma * self.volume_ma_multiplier:
            return None

        buffer = self.breakout_buffer_bps / Decimal("10000")
        long_threshold = previous_high * (Decimal("1") + buffer)
        short_threshold = previous_low * (Decimal("1") - buffer)

        direction = None
        breakout_price = None
        benchmark = None

        if candle.close > long_threshold:
            direction = "BUY"
            benchmark = previous_high
            breakout_price = candle.close
        elif candle.close < short_threshold:
            direction = "SELL"
            benchmark = previous_low
            breakout_price = candle.close

        if direction is None:
            return None

        edge_bps = abs((breakout_price - benchmark) / benchmark) * Decimal("10000")
        strength = min(Decimal("1"), edge_bps / Decimal("60"))
        if strength <= 0:
            strength = Decimal("0.1")

        return {
            "type": direction,
            "symbol": candle.symbol,
            "price": str(breakout_price),
            "strength": str(strength),
            "strategy_name": "Breakout1m",
            "voter_strategies": ["Breakout1m"],
            "timestamp_ms": int(candle.start_ts_ms + 60_000),
            "ab_variant": "A",
            "expected_edge_bps": str(edge_bps.quantize(Decimal("1.00"))),
            "signal_source": "breakout_1m",
            "candle_start_ts_ms": candle.start_ts_ms,
            "candle_high": str(candle.high),
            "candle_low": str(candle.low),
        }

    async def _publish_signal(self, symbol: str, signal: dict[str, Any]) -> None:
        await self.redis_client.publish(f"signals:{symbol}", json.dumps(signal))
        await self.redis_client.publish("signals", json.dumps(signal))

    async def _persist_active_candle(self, symbol: str) -> None:
        state = self.candle_state[symbol]
        await self.redis_client.set(f"signal_engine:1m_candle:{symbol}", state.to_json())

    async def _delete_active_candle(self, symbol: str) -> None:
        await self.redis_client.delete(f"signal_engine:1m_candle:{symbol}")

    async def _persist_history(self, symbol: str, candle: CandleState) -> None:
        key = f"signal_engine:1m_candle_history:{symbol}"
        await self.redis_client.lpush(key, candle.to_json())
        await self.redis_client.ltrim(key, 0, self.breakout_lookback - 1)
