"""Batch persistence of normalized ticks to TimescaleDB."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import asyncpg

logger = logging.getLogger("TickWriter")

INSERT_SQL = """
    INSERT INTO ticks (
        time, symbol, price, volume, side,
        bid_price, ask_price, bid_qty, ask_qty, timestamp_ms
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""


class TickWriter:
    def __init__(self) -> None:
        self.enabled = os.getenv("TICK_PERSIST_ENABLED", "true").lower() == "true"
        self.flush_interval = float(os.getenv("TICK_FLUSH_INTERVAL_SEC", "1.0"))
        self.max_batch = int(os.getenv("TICK_FLUSH_MAX_BATCH", "500"))
        self._buffer: list[tuple] = []
        self._lock = asyncio.Lock()
        self._pool: asyncpg.Pool | None = None
        self._task: asyncio.Task | None = None
        self._total_written = 0
        self._last_flush_ms = 0

    def _dsn(self) -> str:
        user = os.getenv("DB_USER", "crypto_user")
        password = os.getenv("DB_PASSWORD", "crypto_pass")
        host = os.getenv("DB_HOST", "timescaledb")
        port = os.getenv("DB_PORT", "5432")
        db = os.getenv("DB_NAME", "cryptoscalper_db")
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Tick persistence disabled (TICK_PERSIST_ENABLED=false)")
            return
        last_err: Exception | None = None
        for attempt in range(1, 31):
            try:
                pool = await asyncpg.create_pool(dsn=self._dsn(), min_size=1, max_size=3)
                async with pool.acquire() as conn:
                    reg = await conn.fetchval("SELECT to_regclass('public.ticks')")
                    if reg is None:
                        raise RuntimeError("table public.ticks does not exist yet")
                self._pool = pool
                break
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "TickWriter waiting for DB schema (attempt %d/30): %s",
                    attempt,
                    exc,
                )
                await asyncio.sleep(2)
        else:
            raise RuntimeError("TickWriter could not connect — ticks table missing") from last_err
        self._task = asyncio.create_task(self._flush_loop())
        logger.info(
            "TickWriter started (flush every %.1fs, max_batch=%d)",
            self.flush_interval,
            self.max_batch,
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.flush()
        if self._pool:
            await self._pool.close()

    def _tick_to_row(self, tick: dict[str, Any]) -> tuple:
        ts_ms = int(tick.get("timestamp_ms") or time.time() * 1000)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return (
            dt,
            tick["symbol"].upper(),
            float(tick["price"]),
            float(tick.get("qty") or 0),
            tick.get("side"),
            float(tick.get("bid_price") or 0),
            float(tick.get("ask_price") or 0),
            float(tick.get("bid_qty") or 0),
            float(tick.get("ask_qty") or 0),
            ts_ms,
        )

    async def enqueue(self, tick: dict[str, Any]) -> None:
        if not self.enabled or not self._pool:
            return
        row = self._tick_to_row(tick)
        async with self._lock:
            self._buffer.append(row)
            if len(self._buffer) >= self.max_batch:
                await self._flush_locked()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval)
            await self.flush()

    async def flush(self) -> None:
        if not self.enabled or not self._pool:
            return
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer or not self._pool:
            return
        batch = self._buffer
        self._buffer = []
        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(INSERT_SQL, batch)
            self._total_written += len(batch)
            self._last_flush_ms = int(time.time() * 1000)
        except Exception as exc:
            logger.error("Tick flush failed (%d rows): %s", len(batch), exc)
            self._buffer = batch + self._buffer

    async def heartbeat_value(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "buffered": len(self._buffer),
            "total_written": self._total_written,
            "last_flush_ms": self._last_flush_ms,
        }
