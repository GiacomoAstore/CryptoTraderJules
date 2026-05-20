"""Phase 1 morning report builder (shared with scripts/phase1_daily_report.py)."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import redis.asyncio as redis

# Reuse phase1 logic from mounted repo scripts (compose: ./scripts -> /app/scripts)
ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts" / "phase1", Path("/app/scripts/phase1")):
    if _p.is_dir():
        sys.path.insert(0, str(_p))
        break
else:
    raise RuntimeError("scripts/phase1 not found — mount ./scripts in reporter service")

from gate import (  # noqa: E402
    GATE_MAX_DD_PCT,
    GATE_MIN_TRADES,
    GATE_PF_MIN,
    REQUIRED_CLEAN_DAYS,
    STRATEGY_DISABLE_MAX_PF,
    STRATEGY_DISABLE_MIN_TRADES,
    evaluate_end_of_day,
    load_gate,
)
from metrics import STRATEGY_FAMILIES, compute_stats, equity_curve_from_trades, family_from_strategy_name, max_drawdown_pct  # noqa: E402


def _dsn() -> str:
    return (
        f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:"
        f"{os.getenv('DB_PASSWORD', 'crypto_pass')}@"
        f"{os.getenv('DB_HOST', 'timescaledb')}:"
        f"{os.getenv('DB_PORT', '5432')}/"
        f"{os.getenv('DB_NAME', 'cryptoscalper_db')}"
    )


async def fetch_trades(conn) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT time, id, symbol, side, entry_price, exit_price, quantity,
               pnl_usdt, pnl_pct, close_time, strategy_name, ab_variant, close_reason
        FROM trades ORDER BY close_time ASC NULLS LAST, time ASC
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
        out["ingestion_stale"] = True

    out["bot_status"] = await redis_client.get("bot:status") or "unknown"
    out["safe_mode"] = bool(await redis_client.get("bot:safe_mode"))
    cb = await redis_client.hgetall("risk:circuit_breaker") or {}
    out["circuit_breaker"] = cb
    out["circuit_open"] = cb.get("status") == "open"

    restart_raw = await redis_client.get("phase1:last_stack_restart")
    out["last_stack_restart"] = json.loads(restart_raw) if restart_raw else None

    out["paper_balance_a"] = float(await redis_client.get("paper:balance:A") or 0)
    out["paper_balance_b"] = float(await redis_client.get("paper:balance:B") or 0)
    out["paper_total_usdt"] = out["paper_balance_a"] + out["paper_balance_b"]
    start = float(os.getenv("STARTING_CAPITAL", "200"))
    out["starting_capital_usdt"] = start * 2 if start < 150 else start

    try:
        out["alembic_version"] = await conn.fetchval("SELECT version_num FROM alembic_version LIMIT 1")
    except Exception as exc:
        out["alembic_version"] = f"error: {exc}"

    out["positions_db_count"] = int(await conn.fetchval("SELECT COUNT(*) FROM positions") or 0)
    out["db_ticks_count"] = int(await conn.fetchval("SELECT COUNT(*) FROM ticks") or 0)

    # Risk Manager Stats
    hour_key = time.strftime("%Y-%m-%d:%H")
    out["risk_received"] = int(await redis_client.get(f"risk:stats:received:{hour_key}") or 0)
    out["risk_approved"] = int(await redis_client.get(f"risk:stats:approved:{hour_key}") or 0)
    out["risk_rejected_low_profit"] = int(await redis_client.get(f"risk:stats:rejected_low_profit:{hour_key}") or 0)
    out["risk_rejected_other"] = int(await redis_client.get(f"risk:stats:rejected_other:{hour_key}") or 0)
    out["risk_rejected"] = out["risk_rejected_low_profit"] + out["risk_rejected_other"]
    out["risk_approved_pct"] = (out["risk_approved"] / out["risk_received"] * 100) if out["risk_received"] > 0 else 0.0

    # Pending Limit Order Stats (last 24h, keyed by today's date)
    day_key = time.strftime("%Y-%m-%d")
    pending_placed = int(await redis_client.get(f"pending:stats:placed:{day_key}") or 0)
    pending_filled = int(await redis_client.get(f"pending:stats:filled:{day_key}") or 0)
    pending_cancelled = int(await redis_client.get(f"pending:stats:cancelled:{day_key}") or 0)
    pending_escaped = int(await redis_client.get(f"pending:stats:escaped:{day_key}") or 0)
    out["pending_placed"] = pending_placed
    out["pending_filled"] = pending_filled
    out["pending_cancelled"] = pending_cancelled
    out["pending_escaped"] = pending_escaped
    out["pending_fill_rate_pct"] = (pending_filled / pending_placed * 100) if pending_placed > 0 else 0.0

    # ATR fallback tracking
    fallback_since = await redis_client.get("risk:atr_fallback_since")
    if fallback_since:
        fallback_duration_min = (time.time() - float(fallback_since)) / 60.0
        out["atr_fallback_active"] = True
        out["atr_fallback_duration_min"] = round(fallback_duration_min, 1)
    else:
        out["atr_fallback_active"] = False
        last_fallback_sec = await redis_client.get("risk:atr_last_fallback_duration_sec")
        out["atr_last_fallback_min"] = round(int(last_fallback_sec or 0) / 60.0, 1)

    # ATR from DB (5m candles)
    btc_atr = await fetch_5m_atr_reporter(conn, "BTCUSDT")
    eth_atr = await fetch_5m_atr_reporter(conn, "ETHUSDT")
    out["atr_btc"] = f"{btc_atr:.2f} bps" if btc_atr else "N/A"
    out["atr_eth"] = f"{eth_atr:.2f} bps" if eth_atr else "N/A"

    return out

async def fetch_5m_atr_reporter(conn, symbol: str):
    query = """
        WITH candles AS (
            SELECT time_bucket('5 minutes', to_timestamp(timestamp_ms / 1000.0)) AS bucket,
                   MAX(price) AS high, MIN(price) AS low,
                   (array_agg(price ORDER BY timestamp_ms DESC))[1] AS close
            FROM ticks WHERE symbol = $1 AND timestamp_ms > (EXTRACT(EPOCH FROM (now() - INTERVAL '48 hours')) * 1000)
            GROUP BY bucket ORDER BY bucket ASC
        )
        SELECT high, low, close FROM candles
    """
    rows = await conn.fetch(query, symbol)
    if len(rows) < 15: return None
    true_ranges = []
    for i in range(1, len(rows)):
        high, low, prev_close = float(rows[i]["high"]), float(rows[i]["low"]), float(rows[i-1]["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(true_ranges) < 14: return None
    atr = sum(true_ranges[-14:]) / 14
    price = float(rows[-1]["close"])
    return (atr / price) * 10000


def group_by_family(trades: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {k: [] for k in STRATEGY_FAMILIES}
    buckets["Consensus"] = []
    buckets["Other"] = []
    for t in trades:
        fam = family_from_strategy_name(t.get("strategy_name"))
        buckets.setdefault(fam, []).append(t)
    return buckets


async def check_strategy_thresholds(redis_client, by_family: dict) -> list[str]:
    """Immediate alert if >=50 trades and PF<0.9 (once per strategy)."""
    actions: list[str] = []
    name_map = {"EMA": "EMACrossoverStrategy", "Momentum": "MomentumBurstStrategy", "VWAP": "VWAPDeviationStrategy"}
    for family, strat_name in name_map.items():
        stats = compute_stats(by_family.get(family, []))
        if stats.trades < STRATEGY_DISABLE_MIN_TRADES:
            continue
        pf = stats.profit_factor if stats.profit_factor != float("inf") else 999.0
        if pf >= STRATEGY_DISABLE_MAX_PF:
            continue
        alert_key = f"phase1:alerted:pf50:{strat_name}"
        if await redis_client.get(alert_key):
            continue
        await redis_client.set(alert_key, "1")
        msg = f"STRATEGY ALERT: {family} ({strat_name}) — {stats.trades} trades, PF={pf:.3f} (<{STRATEGY_DISABLE_MAX_PF})"
        actions.append(msg)
        await redis_client.publish(
            "phase1:urgent",
            json.dumps({"event": "strategy_pf_alert", "message": msg, "family": family}),
        )
        key = f"phase1:disable:{strat_name}"
        if not await redis_client.get(key):
            await redis_client.set(
                key,
                json.dumps({"reason": msg, "disabled_at": datetime.now(timezone.utc).isoformat()}),
            )
    return actions


def format_telegram(report: dict, day_label: str) -> str:
    g = report["global"]
    infra = report["infrastructure"]
    gate = report["gate"]
    lines = [
        f"FASE 1 — Giorno {day_label}",
        f"Data UTC: {report['generated_at'][:10]}",
        "",
        "GLOBAL",
        f"Trades: {g['trades']} | PF: {g.get('profit_factor', 0):.3f} (gate >{GATE_PF_MIN}, n>={GATE_MIN_TRADES})",
        f"Max DD: {g.get('max_drawdown_pct', 0):.2f}% (gate <{GATE_MAX_DD_PCT}%)",
        f"Win rate: {g.get('win_rate_pct', 0):.1f}% | E: {g.get('expectancy_bps', 0):.2f} bps",
        f"PnL: ${g.get('total_pnl_usdt', 0):.2f} | Equity paper: ${infra.get('paper_total_usdt', 0):.2f}",
        "",
        "STRATEGIE",
    ]
    for family in ("EMA", "Momentum", "VWAP"):
        s = report["by_strategy"].get(family, {})
        flag = " [OFF]" if s.get("disabled") else ""
        pf = s.get("profit_factor") or 0
        lines.append(
            f"{family}: n={s.get('trades', 0)} WR={s.get('win_rate_pct', 0):.0f}% "
            f"PF={pf:.3f} E={s.get('expectancy_bps', 0):.1f}bps{flag}"
        )
    lines.extend([
        "",
        "INFRA",
        f"Bot: {infra.get('bot_status')} | CB: {infra.get('circuit_breaker', {}).get('status', '?')}",
        f"Safe mode: {'ON' if infra.get('safe_mode') else 'off'} | Ingestion stale: {'YES' if infra.get('ingestion_stale') else 'no'}",
        f"Ticks DB: {infra.get('db_ticks_count', 0):,} | Pos aperte: {infra.get('positions_db_count', 0)}",
        "",
        "⚡ RISK MANAGER (ultima ora)",
        f"Segnali ricevuti: {infra.get('risk_received', 0)}",
        f"Approvati: {infra.get('risk_approved', 0)} ({infra.get('risk_approved_pct', 0):.1f}%)",
        f"Rifiutati: {infra.get('risk_rejected', 0)}",
        f"  → Low profitability: {infra.get('risk_rejected_low_profit', 0)}",
        f"  → Altri: {infra.get('risk_rejected_other', 0)}",
        f"ATR live: BTC={infra.get('atr_btc', 'N/A')} | ETH={infra.get('atr_eth', 'N/A')}",
        (
            f"⚠️ ATR FALLBACK ATTIVO da {infra.get('atr_fallback_duration_min', 0):.0f} min!"
            if infra.get("atr_fallback_active")
            else (
                f"ATR fallback ultima notte: {infra.get('atr_last_fallback_min', 0):.0f} min"
                if infra.get("atr_last_fallback_min", 0) > 0
                else "ATR source: 5m-candle ✅"
            )
        ),
        "",
        "📋 ORDINI PENDENTI (ultime 24h)",
        f"Piazzati: {infra.get('pending_placed', 0)}",
        f"Fillati: {infra.get('pending_filled', 0)}  ({infra.get('pending_fill_rate_pct', 0):.0f}% fill rate)",
        f"Cancellati (timeout): {infra.get('pending_cancelled', 0)}",
        f"Scappati senza fill: {infra.get('pending_escaped', 0)}",
        "",
        "GATE",
        f"Giorni puliti consecutivi: {gate.get('consecutive_clean_days', 0)} / {REQUIRED_CLEAN_DAYS}",
        f"Exit ready: {'SI' if gate.get('gate_exit_ready') else 'NO'}",
    ])
    if gate.get("last_reset_reason"):
        lines.append(f"Ultimo reset: {gate.get('last_reset_reason')}")
    return "\n".join(lines)


async def build_report(*, update_gate: bool = True) -> dict:
    conn = await asyncpg.connect(_dsn())
    rhost = os.getenv("REDIS_HOST", "redis")
    rport = int(os.getenv("REDIS_PORT", 6379))
    redis_client = redis.Redis(host=rhost, port=rport, decode_responses=True)
    try:
        trades = await fetch_trades(conn)
        infra = await fetch_infra(conn, redis_client)
        starting = infra["starting_capital_usdt"]
        dd_pct = max_drawdown_pct(equity_curve_from_trades(trades, starting), starting)

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

        strategy_actions = await check_strategy_thresholds(redis_client, by_family)

        infra_notes = []
        infra_clean = True
        if infra.get("circuit_open"):
            infra_clean = False
            infra_notes.append(f"CB open: {infra['circuit_breaker'].get('reason')}")
        if infra.get("safe_mode"):
            infra_clean = False
            infra_notes.append("safe_mode")
        if infra.get("ingestion_stale"):
            infra_clean = False
            infra_notes.append("ingestion stale")

        global_dict = global_stats.to_dict()
        global_dict["profit_factor"] = (
            global_stats.profit_factor if global_stats.profit_factor != float("inf") else 999.0
        )
        global_dict["max_drawdown_pct"] = round(dd_pct, 2)

        config_path = Path("/app/shared_config/config.yaml")
        if not config_path.is_file():
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
            "global": global_dict,
            "by_strategy": by_strategy_report,
            "infrastructure": infra,
            "gate": gate_state,
            "strategy_actions": strategy_actions,
        }
    finally:
        await conn.close()
        await redis_client.aclose()

