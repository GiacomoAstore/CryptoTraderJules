"""Phase 1 gate state — consecutive clean days (not calendar-only)."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

GATE_REDIS_KEY = "phase1:gate"
EVENTS_REDIS_KEY = "phase1:events"
CONFIG_HASH_KEY = "phase1:config_hash"
REQUIRED_CLEAN_DAYS = 14
GATE_PF_MIN = 1.2
GATE_MIN_TRADES = 100
GATE_MAX_DD_PCT = 15.0
STRATEGY_DISABLE_MIN_TRADES = 50
STRATEGY_DISABLE_MAX_PF = 0.9


def config_fingerprint(config_path: Path) -> str:
    data = config_path.read_bytes()
    return hashlib.sha256(data).hexdigest()[:16]


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def load_gate(redis) -> dict[str, Any]:
    raw = await redis.get(GATE_REDIS_KEY)
    if not raw:
        return {
            "consecutive_clean_days": 0,
            "last_evaluated_date": None,
            "last_reset_at": None,
            "last_reset_reason": "initial",
            "history": [],
        }
    return json.loads(raw)


async def save_gate(redis, state: dict[str, Any]) -> None:
    await redis.set(GATE_REDIS_KEY, json.dumps(state))


async def append_event(redis, event_type: str, detail: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "detail": detail,
    }
    await redis.lpush(EVENTS_REDIS_KEY, json.dumps(entry))
    await redis.ltrim(EVENTS_REDIS_KEY, 0, 199)


async def record_stack_restart(redis, service: str, reason: str = "container_boot") -> None:
    await append_event(redis, "stack_restart", f"{service}: {reason}")
    await redis.set(
        "phase1:last_stack_restart",
        json.dumps({"ts": time.time(), "service": service, "reason": reason}),
    )


async def check_config_stable(redis, config_path: Path) -> tuple[bool, str]:
    fp = config_fingerprint(config_path)
    prev = await redis.get(CONFIG_HASH_KEY)
    if prev is None:
        await redis.set(CONFIG_HASH_KEY, fp)
        return True, fp
    if prev != fp:
        await append_event(redis, "config_changed", f"hash {prev} -> {fp}")
        await redis.set(CONFIG_HASH_KEY, fp)
        return False, fp
    return True, fp


def day_metrics_pass(global_stats: dict, max_dd_pct: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    trades = global_stats.get("trades", 0)
    pf = global_stats.get("profit_factor") or 0
    if trades < GATE_MIN_TRADES:
        reasons.append(f"trades {trades} < {GATE_MIN_TRADES}")
    if pf < GATE_PF_MIN:
        reasons.append(f"PF {pf:.3f} < {GATE_PF_MIN}")
    if max_dd_pct > GATE_MAX_DD_PCT:
        reasons.append(f"max_dd {max_dd_pct:.2f}% > {GATE_MAX_DD_PCT}%")
    return len(reasons) == 0, reasons


async def evaluate_end_of_day(
    redis,
    *,
    global_stats: dict,
    max_dd_pct: float,
    infra_clean: bool,
    infra_notes: list[str],
    config_path: Path,
) -> dict[str, Any]:
    state = await load_gate(redis)
    day = today_utc()

    config_ok, fp = await check_config_stable(redis, config_path)
    metrics_ok, metric_fail = day_metrics_pass(global_stats, max_dd_pct)
    clean_day = infra_clean and config_ok and metrics_ok

    reset_reasons: list[str] = []
    if not config_ok:
        reset_reasons.append("config.yaml modified")
    if not infra_clean:
        reset_reasons.extend(infra_notes)

    if reset_reasons:
        state["consecutive_clean_days"] = 0
        state["last_reset_at"] = datetime.now(timezone.utc).isoformat()
        state["last_reset_reason"] = "; ".join(reset_reasons)
        await append_event(redis, "gate_reset", state["last_reset_reason"])
    elif state.get("last_evaluated_date") != day:
        if clean_day:
            state["consecutive_clean_days"] = int(state.get("consecutive_clean_days", 0)) + 1
        else:
            state["consecutive_clean_days"] = 0
            fail = metric_fail + ([] if infra_clean else infra_notes)
            state["last_reset_at"] = datetime.now(timezone.utc).isoformat()
            state["last_reset_reason"] = "; ".join(fail) if fail else "day not clean"
            await append_event(redis, "gate_reset", state["last_reset_reason"])

    state["last_evaluated_date"] = day
    state["config_hash"] = fp
    state.setdefault("history", [])
    state["history"].append(
        {
            "date": day,
            "clean": clean_day,
            "consecutive_after": state["consecutive_clean_days"],
            "metrics_ok": metrics_ok,
            "infra_ok": infra_clean,
            "config_ok": config_ok,
        }
    )
    state["history"] = state["history"][-30:]
    state["gate_target_days"] = REQUIRED_CLEAN_DAYS
    state["gate_exit_ready"] = state["consecutive_clean_days"] >= REQUIRED_CLEAN_DAYS

    await save_gate(redis, state)
    return state
