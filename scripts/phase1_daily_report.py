#!/usr/bin/env python3
"""
Phase 1 daily paper-trading report (~30s read).

  python scripts/phase1_daily_report.py
  python scripts/phase1_daily_report.py --json
  python scripts/phase1_daily_report.py --no-gate-update   # read-only snapshot
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import redis.asyncio as redis
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "phase1"))

from gate import (  # noqa: E402
    GATE_MAX_DD_PCT,
    GATE_MIN_TRADES,
    GATE_PF_MIN,
    REQUIRED_CLEAN_DAYS,
    STRATEGY_DISABLE_MAX_PF,
    STRATEGY_DISABLE_MIN_TRADES,
    append_event,
    evaluate_end_of_day,
    load_gate,
)
from metrics import family_from_strategy_name  # noqa: E402
from metrics import STRATEGY_FAMILIES, compute_stats, equity_curve_from_trades, max_drawdown_pct  # noqa: E402


def _dsn() -> str:
    user = os.getenv("DB_USER", "crypto_user")
    password = os.getenv("DB_PASSWORD", "crypto_pass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    db = os.getenv("DB_NAME", "cryptoscalper_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _redis_url() -> tuple[str, int]:
    return os.getenv("REDIS_HOST", "localhost"), int(os.getenv("REDIS_PORT", 6379))


async def fetch_trades(conn) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT time, id, symbol, side, entry_price, exit_price, quantity,
               pnl_usdt, pnl_pct, close_time, strategy_name, ab_variant, close_reason
        FROM trades
        ORDER BY close_time ASC NULLS LAST, time ASC
        """
    )
    return [dict(r) for r in rows]


async def fetch_infra(conn, redis_client) -> dict:
    out: dict = {}
    hb = await redis_client.get("ingestion:heartbeat")
    if hb:
        age_ms = int(time.time() * 1000) - int(hb)
        out["ingestion_heartbeat_age_sec"] = round(age_ms / 1000, 1)
        out["ingestion_stale"] = age_ms > 10000
    else:
        out["ingestion_heartbeat_age_sec"] = None
        out["ingestion_stale"] = True

    out["bot_status"] = await redis_client.get("bot:status") or "unknown"
    out["safe_mode"] = bool(await redis_client.get("bot:safe_mode"))
    cb = await redis_client.hgetall("risk:circuit_breaker")
    out["circuit_breaker"] = cb or {"status": "unknown"}
    out["circuit_open"] = cb.get("status") == "open"

    restart_raw = await redis_client.get("phase1:last_stack_restart")
    out["last_stack_restart"] = json.loads(restart_raw) if restart_raw else None

    bal_a = float(await redis_client.get("paper:balance:A") or 0)
    bal_b = float(await redis_client.get("paper:balance:B") or 0)
    out["paper_balance_a"] = bal_a
    out["paper_balance_b"] = bal_b
    out["paper_total_usdt"] = bal_a + bal_b

    start = float(os.getenv("STARTING_CAPITAL", "200"))
    out["starting_capital_usdt"] = start * 2 if start < 150 else start

    try:
        ver = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")
        out["alembic_version"] = ver
    except Exception as exc:
        out["alembic_version"] = f"error: {exc}"

    pos_db = await conn.fetchval("SELECT COUNT(*) FROM positions")
    open_keys = []
    for variant in ("A", "B"):
        for sym in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
            if await redis_client.exists(f"tick:last:{sym}"):
                pass
    rows = await conn.fetch("SELECT symbol, ab_variant, side, entry_price FROM positions")
    out["open_positions_db"] = [dict(r) for r in rows]
    out["positions_db_count"] = int(pos_db or 0)
    out["db_ticks_count"] = int(await conn.fetchval("SELECT COUNT(*) FROM ticks") or 0)

    events = await redis_client.lrange("phase1:events", 0, 9)
    out["recent_events"] = [json.loads(e) for e in events] if events else []

    disabled = []
    for family in STRATEGY_FAMILIES:
        for name in STRATEGY_FAMILIES[family]:
            key = f"phase1:disable:{name}"
            if await redis_client.get(key):
                disabled.append(name)
    out["strategies_disabled_via_redis"] = disabled

    return out


def group_by_family(trades: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {k: [] for k in STRATEGY_FAMILIES}
    buckets["Consensus"] = []
    buckets["Other"] = []
    for t in trades:
        fam = family_from_strategy_name(t.get("strategy_name"))
        buckets.setdefault(fam, []).append(t)
    return buckets


async def apply_strategy_guard(redis_client, by_family: dict) -> list[str]:
    actions: list[str] = []
    name_map = {
        "EMA": "EMACrossoverStrategy",
        "Momentum": "MomentumBurstStrategy",
        "VWAP": "VWAPDeviationStrategy",
    }
    for family, strat_name in name_map.items():
        rows = by_family.get(family, [])
        stats = compute_stats(rows)
        if stats.trades < STRATEGY_DISABLE_MIN_TRADES:
            continue
        pf = stats.profit_factor if stats.profit_factor != float("inf") else 999.0
        if pf < STRATEGY_DISABLE_MAX_PF:
            key = f"phase1:disable:{strat_name}"
            await redis_client.set(key, json.dumps({
                "disabled_at": datetime.now(timezone.utc).isoformat(),
                "reason": f"PF {pf:.3f} < {STRATEGY_DISABLE_MAX_PF} after {stats.trades} trades",
                "family": family,
            }))
            await append_event(
                redis_client,
                "strategy_disabled",
                f"{strat_name} PF={pf:.3f} n={stats.trades}",
            )
            actions.append(f"DISABLED {family} ({strat_name}): PF={pf:.3f} on {stats.trades} trades")
    return actions


def format_text(report: dict) -> str:
    g = report["global"]
    infra = report["infrastructure"]
    gate = report["gate"]
    lines = [
        "=" * 60,
        f" PHASE 1 DAILY REPORT — {report['generated_at'][:10]} (paper)",
        "=" * 60,
        "",
        "-- GLOBAL (closed trades) --",
        f"  Trades      : {g['trades']}",
        f"  Profit F.   : {g['profit_factor']:.3f}  (gate: >{GATE_PF_MIN}, n≥{GATE_MIN_TRADES})",
        f"  Max DD      : {g['max_drawdown_pct']:.2f}%  (gate: <{GATE_MAX_DD_PCT}%)",
        f"  Win rate    : {g['win_rate_pct']:.1f}%",
        f"  Expectancy  : {g['expectancy_bps']:.2f} bps/trade",
        f"  Total PnL   : ${g['total_pnl_usdt']:.2f}",
        f"  Paper equity: ${infra['paper_total_usdt']:.2f} (start ~${infra['starting_capital_usdt']:.0f})",
        "",
        "-- PER STRATEGY FAMILY --",
    ]
    for family in ("EMA", "Momentum", "VWAP"):
        s = report["by_strategy"][family]
        flag = " [!]" if s.get("disabled") else ""
        lines.append(
            f"  {family:<10} trades={s['trades']:>4}  WR={s['win_rate_pct']:>5.1f}%  "
            f"E={s['expectancy_bps']:>7.2f}bps  PF={s['profit_factor']:.3f}{flag}"
        )
    if report["by_strategy"].get("Consensus", {}).get("trades", 0):
        s = report["by_strategy"]["Consensus"]
        lines.append(
            f"  {'Consensus':<10} trades={s['trades']:>4}  (legacy label — new trades tag voters)"
        )
    lines.extend([
        "",
        "-- INFRASTRUCTURE --",
        f"  Bot status       : {infra['bot_status']}",
        f"  Circuit breaker  : {infra['circuit_breaker'].get('status', '?')}"
        + (f" | {infra['circuit_breaker'].get('reason', '')}" if infra.get("circuit_open") else ""),
        f"  Safe mode        : {'ON' if infra['safe_mode'] else 'off'}",
        f"  Ingestion stale  : {'YES' if infra['ingestion_stale'] else 'no'}"
        + (f" ({infra['ingestion_heartbeat_age_sec']}s)" if infra.get("ingestion_heartbeat_age_sec") is not None else ""),
        f"  Alembic          : {infra.get('alembic_version')}",
        f"  Open positions   : {infra['positions_db_count']} (DB)",
        f"  Ticks persisted  : {infra['db_ticks_count']:,}",
    ])
    if infra.get("last_stack_restart"):
        r = infra["last_stack_restart"]
        lines.append(f"  Last restart     : {r.get('service')} @ {r.get('ts')} | {r.get('reason')}")
    if infra.get("strategies_disabled_via_redis"):
        lines.append(f"  Redis disables   : {', '.join(infra['strategies_disabled_via_redis'])}")
    lines.extend([
        "",
        "-- GATE (14 clean days, not calendar) --",
        f"  Consecutive clean days : {gate['consecutive_clean_days']} / {REQUIRED_CLEAN_DAYS}",
        f"  Gate exit ready        : {'YES' if gate.get('gate_exit_ready') else 'NO'}",
    ])
    if gate.get("last_reset_reason"):
        lines.append(f"  Last reset             : {gate.get('last_reset_reason')}")
    if report.get("strategy_actions"):
        lines.append("")
        lines.append("-- ACTIONS TODAY --")
        for a in report["strategy_actions"]:
            lines.append(f"  * {a}")
    if infra.get("circuit_open"):
        lines.append("")
        lines.append("  [!] CIRCUIT BREAKER OPEN - read reason before any reset:")
        lines.append(f"    redis-cli HGETALL risk:circuit_breaker")
        lines.append("    Do NOT reset until cause is understood and logged.")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


async def build_report(*, update_gate: bool = True) -> dict:
    conn = await asyncpg.connect(_dsn())
    rhost, rport = _redis_url()
    redis_client = redis.Redis(host=rhost, port=rport, decode_responses=True)
    try:
        trades = await fetch_trades(conn)
        infra = await fetch_infra(conn, redis_client)
        starting = infra["starting_capital_usdt"]
        curve = equity_curve_from_trades(trades, starting)
        dd_pct = max_drawdown_pct(curve, starting)

        global_stats = compute_stats(trades)
        by_family = group_by_family(trades)
        by_strategy_report = {}
        for family in ("EMA", "Momentum", "VWAP", "Consensus", "Other"):
            st = compute_stats(by_family.get(family, []))
            d = st.to_dict()
            d["disabled"] = False
            for n in STRATEGY_FAMILIES.get(family, ()):
                if await redis_client.get(f"phase1:disable:{n}"):
                    d["disabled"] = True
            by_strategy_report[family] = d

        strategy_actions = await apply_strategy_guard(redis_client, by_family)

        infra_notes = []
        infra_clean = True
        if infra.get("circuit_open"):
            infra_clean = False
            infra_notes.append(f"circuit breaker open: {infra['circuit_breaker'].get('reason')}")
        if infra.get("safe_mode"):
            infra_clean = False
            infra_notes.append("bot safe_mode active")
        if infra.get("ingestion_stale"):
            infra_clean = False
            infra_notes.append("ingestion heartbeat stale")

        global_dict = global_stats.to_dict()
        global_dict["profit_factor"] = (
            global_stats.profit_factor if global_stats.profit_factor != float("inf") else 999.0
        )
        global_dict["max_drawdown_pct"] = round(dd_pct, 2)

        config_path = ROOT / "shared_config" / "config.yaml"
        gate_state = await load_gate(redis_client)
        if update_gate:
            gate_state = await evaluate_end_of_day(
                redis_client,
                global_stats=global_dict,
                max_dd_pct=dd_pct,
                infra_clean=infra_clean,
                infra_notes=infra_notes,
                config_path=config_path,
            )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "phase": 1,
            "mode": "paper",
            "global": global_dict,
            "by_strategy": by_strategy_report,
            "infrastructure": infra,
            "gate": gate_state,
            "strategy_actions": strategy_actions,
            "notes": [
                "Gate day increments once per UTC day when metrics+infra+config stable.",
                "Resets on CB open, config change, safe_mode, stale ingestion.",
                f"Strategy auto-disable: Redis phase1:disable:* at {STRATEGY_DISABLE_MIN_TRADES} trades if PF<{STRATEGY_DISABLE_MAX_PF}.",
            ],
        }
    finally:
        await conn.close()
        await redis_client.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 daily paper report")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--no-gate-update", action="store_true", help="Do not update gate counters")
    args = parser.parse_args()

    report = await build_report(update_gate=not args.no_gate_update)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
