from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

MINUTE_MS = 60_000


def minute_bucket_start(timestamp_ms: int) -> int:
    return (timestamp_ms // MINUTE_MS) * MINUTE_MS


@dataclass
class CandleState:
    symbol: str
    start_ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    last_tick_ts_ms: int

    @classmethod
    def from_tick(cls, tick: dict[str, Any]) -> "CandleState":
        timestamp_ms = int(tick["timestamp_ms"])
        price = Decimal(str(tick["price"]))
        qty = Decimal(str(tick.get("qty", 0)))
        return cls(
            symbol=tick["symbol"],
            start_ts_ms=minute_bucket_start(timestamp_ms),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=qty,
            last_tick_ts_ms=timestamp_ms,
        )

    def update(self, tick: dict[str, Any]) -> None:
        timestamp_ms = int(tick["timestamp_ms"])
        price = Decimal(str(tick["price"]))
        qty = Decimal(str(tick.get("qty", 0)))

        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += qty
        self.last_tick_ts_ms = timestamp_ms

    def is_same_minute(self, timestamp_ms: int) -> bool:
        return minute_bucket_start(timestamp_ms) == self.start_ts_ms

    def to_dict(self) -> dict[str, str | int]:
        return {
            "symbol": self.symbol,
            "start_ts_ms": self.start_ts_ms,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "last_tick_ts_ms": self.last_tick_ts_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandleState":
        return cls(
            symbol=data["symbol"],
            start_ts_ms=int(data["start_ts_ms"]),
            open=Decimal(str(data["open"])),
            high=Decimal(str(data["high"])),
            low=Decimal(str(data["low"])),
            close=Decimal(str(data["close"])),
            volume=Decimal(str(data["volume"])),
            last_tick_ts_ms=int(data["last_tick_ts_ms"]),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str) -> "CandleState":
        return cls.from_dict(json.loads(payload))
