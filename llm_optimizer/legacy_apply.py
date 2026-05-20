"""Legacy auto-rewrite of config.yaml — gated; not for Phase 1."""
from __future__ import annotations

import logging
import os

import asyncpg
import redis.asyncio as redis
import yaml
from openai import AsyncOpenAI

import sys

sys.path.insert(0, "/app/shared_config")
from validate_config import validate_config_yaml

logger = logging.getLogger("LLMLegacyApply")

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:"
    f"{os.getenv('DB_PASSWORD', 'crypto_pass')}@"
    f"{os.getenv('DB_HOST', 'timescaledb')}:"
    f"{os.getenv('DB_PORT', '5432')}/"
    f"{os.getenv('DB_NAME', 'cryptoscalper_db')}"
)
SHARED_CONFIG_PATH = "/app/shared_config/config.yaml"

SYSTEM_PROMPT = """You are an expert algorithmic trading quantitative analyst.
Output ONLY valid YAML for config.yaml. No markdown fences. No commentary.
"""


async def fetch_performance_metrics():
    try:
        pool = await asyncpg.create_pool(dsn=DB_DSN)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT strategy_name AS strategy,
                       COUNT(*) AS total_trades,
                       COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS wins,
                       SUM(pnl_usdt) AS total_pnl
                FROM trades GROUP BY strategy_name
                """
            )
            metrics = {}
            for row in rows:
                total = row["total_trades"]
                wins = row["wins"]
                metrics[row["strategy"]] = {
                    "total_trades": total,
                    "win_rate": round(wins / total, 2) if total else 0,
                    "total_pnl": float(row["total_pnl"] or 0),
                }
        await pool.close()
        return metrics
    except Exception as e:
        logger.error("Failed to fetch metrics: %s", e)
        return {}


async def optimize_config():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key":
        logger.warning("Groq API Key not set. Skipping.")
        return

    metrics = await fetch_performance_metrics()
    if not os.path.exists(SHARED_CONFIG_PATH):
        return

    with open(SHARED_CONFIG_PATH, "r", encoding="utf-8") as f:
        current_config_str = f.read()

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    prompt = f"Metrics:\n{metrics}\n\nCurrent config:\n{current_config_str}\n\nOptimized YAML:"

    response = await client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    new_yaml = (response.choices[0].message.content or "").strip()
    for fence in ("```yaml", "```"):
        if new_yaml.startswith(fence):
            new_yaml = new_yaml.split(fence, 1)[1]
    if "```" in new_yaml:
        new_yaml = new_yaml.rsplit("```", 1)[0]
    new_yaml = new_yaml.strip()

    validate_config_yaml(new_yaml)

    proposed = SHARED_CONFIG_PATH + ".proposed"
    with open(proposed, "w", encoding="utf-8") as f:
        f.write(new_yaml)
    logger.info("Wrote proposed config to %s (NOT applied to live config)", proposed)

    if os.getenv("LLM_APPLY_PROPOSED_TO_LIVE", "false").lower() == "true":
        with open(SHARED_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(new_yaml)
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True,
        )
        await redis_client.publish("system:commands", "RELOAD_CONFIG")
        logger.warning("Applied proposed config to LIVE config.yaml")
