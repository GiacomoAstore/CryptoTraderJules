"""Read-only metrics for LLM advisory (aligned with Phase 1 report)."""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal
from typing import Any

import asyncpg
import redis.asyncio as redis


def _dsn() -> str:
    return (
        f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:"
        f"{os.getenv('DB_PASSWORD', 'crypto_pass')}@"
        f"{os.getenv('DB_HOST', 'timescaledb')}:"
        f"{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'cryptoscalper_db')}"
    )


async def build_snapshot() -> dict[str, Any]:
    conn = await asyncpg.connect(_dsn())
    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        decode_responses=True,
    )
    try:
        rows = await conn.fetch(
            """
            SELECT strategy_name, pnl_usdt, entry_price, quantity, close_reason, close_time
            FROM trades ORDER BY close_time ASC NULLS LAST
            """
        )
        trades = [dict(x) for x in rows]

        by_strategy: dict[str, dict] = {}
        for t in trades:
            name = t.get("strategy_name") or "Unknown"
            bucket = by_strategy.setdefault(
                name,
                {"trades": 0, "wins": 0, "pnl": Decimal("0"), "gross_win": Decimal("0"), "gross_loss": Decimal("0")},
            )
            pnl = Decimal(str(t["pnl_usdt"] or 0))
            bucket["trades"] += 1
            bucket["pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
                bucket["gross_win"] += pnl
            elif pnl < 0:
                bucket["gross_loss"] += abs(pnl)

        strategy_summary = {}
        for name, b in by_strategy.items():
            pf = float(b["gross_win"] / b["gross_loss"]) if b["gross_loss"] > 0 else None
            strategy_summary[name] = {
                "trades": b["trades"],
                "win_rate": round(b["wins"] / b["trades"], 3) if b["trades"] else 0,
                "pnl_usdt": float(b["pnl"]),
                "profit_factor": round(pf, 3) if pf is not None else None,
            }

        total_pnl = sum(float(t.get("pnl_usdt") or 0) for t in trades)
        wins = sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0)
        n = len(trades)

        gate_raw = await r.get("phase1:gate")
        gate = json.loads(gate_raw) if gate_raw else {}

        cb = await r.hgetall("risk:circuit_breaker") or {}
        hb = await r.get("ingestion:heartbeat")
        hb_age = None
        if hb:
            hb_age = round((time.time() * 1000 - int(hb)) / 1000, 1)

        cfg_path = os.getenv("SHARED_CONFIG_PATH", "/app/shared_config/config.yaml")
        config_excerpt = ""
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                config_excerpt = f.read()[:4000]

        return {
            "trade_count": n,
            "global_win_rate": round(wins / n, 3) if n else 0,
            "global_pnl_usdt": round(total_pnl, 2),
            "by_strategy": strategy_summary,
            "phase1_gate": gate,
            "circuit_breaker": cb,
            "bot_status": await r.get("bot:status"),
            "ingestion_heartbeat_age_sec": hb_age,
            "config_excerpt": config_excerpt,
            "close_reasons": _count_field(trades, "close_reason"),
        }
    finally:
        await conn.close()
        await r.aclose()


def _count_field(trades: list, field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trades:
        k = str(t.get(field) or "unknown")
        out[k] = out.get(k, 0) + 1
    return out
