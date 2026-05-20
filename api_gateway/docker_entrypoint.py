#!/usr/bin/env python3
"""Container boot: legacy repair → alembic upgrade → schema verify → uvicorn."""
from __future__ import annotations

import subprocess
import sys


def run(cmd: list[str], label: str) -> None:
    print(f"[boot] {label}...", flush=True)
    result = subprocess.run(cmd, cwd="/app")
    if result.returncode != 0:
        print(f"[boot] FATAL: {label} failed (exit {result.returncode})", flush=True)
        sys.exit(result.returncode)


def _log_restart() -> None:
    try:
        import asyncio
        import redis.asyncio as redis
        import os
        import json
        import time

        async def _push():
            host = os.getenv("REDIS_HOST", "redis")
            port = int(os.getenv("REDIS_PORT", 6379))
            r = redis.Redis(host=host, port=port, decode_responses=True)
            payload = json.dumps({
                "ts": time.time(),
                "service": "api_gateway",
                "reason": "container_boot",
            })
            await r.set("phase1:last_stack_restart", payload)
            evt = json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": "stack_restart",
                "detail": "api_gateway: container_boot",
            })
            await r.lpush("phase1:events", evt)

            armed = await r.get("phase1:boot_alert_armed")
            hb = await r.get("ingestion:heartbeat")
            hb_age_ms = None
            if hb:
                hb_age_ms = int(time.time() * 1000) - int(hb)
            # Unplanned: stack was running, ingestion was live seconds before this boot
            if armed and hb_age_ms is not None and hb_age_ms < 20000:
                await r.publish(
                    "phase1:urgent",
                    json.dumps({
                        "event": "stack_restart",
                        "message": (
                            "Restart non pianificato rilevato (api_gateway).\n"
                            f"Ingestion heartbeat {hb_age_ms / 1000:.1f}s fa."
                        ),
                    }),
                )
            await r.aclose()

        asyncio.run(_push())
    except Exception as exc:
        print(f"[boot] phase1 restart log skipped: {exc}", flush=True)


def main() -> None:
    _log_restart()
    run([sys.executable, "repair_legacy_alembic.py"], "Legacy Alembic repair")
    run(["alembic", "upgrade", "head"], "Alembic upgrade head")
    run([sys.executable, "verify_db_schema.py"], "Database schema verify")
    print("[boot] Schema OK — starting API Gateway", flush=True)
    os_exec = subprocess.run(
        ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd="/app",
    )
    sys.exit(os_exec.returncode)


if __name__ == "__main__":
    main()
