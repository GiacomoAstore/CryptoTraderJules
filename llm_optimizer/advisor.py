"""LLM advisor — suggestions only, never mutates live config during Phase 1."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI

from metrics_snapshot import build_snapshot

logger = logging.getLogger("LLMAdvisor")

SYSTEM_PROMPT = """You are a senior quantitative analyst for a crypto HFT scalping system.

STRICT RULES:
- You MUST NOT output a full config.yaml or executable parameter changes.
- You advise a human operator only.
- Scalping context: fees and spread dominate; few high-quality trades beat many marginal ones.
- If sample size is small (<100 closed trades), say conclusions are not statistically reliable.
- Reference profit factor, win rate, SL/TP close reasons, and infrastructure health.
- Suggest at most 3 concrete hypotheses and 3 cautious next steps (research/backtest/paper), not live deploy commands.

Output format (markdown, concise, <400 words):
## Executive summary (2 sentences)
## Strategy diagnostics (bullet per family/strategy)
## Infrastructure risks (if any)
## Recommended actions (numbered, no auto-apply)
## What NOT to do now
"""


async def run_advisory() -> str | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key in ("your_groq_api_key", ""):
        logger.warning("GROQ_API_KEY not set — advisor skipped")
        return None

    snapshot = await build_snapshot()
    if snapshot["trade_count"] == 0:
        logger.info("No closed trades yet — generating cold-start advisory")

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

    user_prompt = (
        "Analyze this paper-trading snapshot and advise the operator.\n"
        "Phase 1 rule: configuration file must remain frozen; only Redis strategy disables are allowed.\n\n"
        f"```json\n{json.dumps(snapshot, indent=2, default=str)}\n```"
    )

    response = await client.chat.completions.create(
        model=model,
        temperature=0.3,
        max_tokens=900,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    text = (response.choices[0].message.content or "").strip()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    reports_dir = Path(os.getenv("LLM_REPORTS_DIR", "/app/reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"llm_advisory_{ts}.md"

    header = (
        f"# LLM Advisory — {datetime.now(timezone.utc).isoformat()}\n\n"
        f"**Mode:** advisor (read-only, no config changes)\n\n"
        f"**Trades analyzed:** {snapshot['trade_count']}\n\n---\n\n"
    )
    full = header + text
    out_path.write_text(full, encoding="utf-8")

    try:
        import redis.asyncio as redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True,
        )
        await r.set("phase1:llm_advisory:latest", full)
        await r.lpush("phase1:llm_advisory:history", json.dumps({"ts": ts, "path": str(out_path)}))
        await r.ltrim("phase1:llm_advisory:history", 0, 30)
        await r.aclose()
    except Exception as exc:
        logger.warning("Could not publish advisory to Redis: %s", exc)

    logger.info("Advisory written to %s", out_path)
    return str(out_path)
