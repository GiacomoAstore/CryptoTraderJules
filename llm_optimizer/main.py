"""
LLM Optimizer — modes:
  advisor (default)  : read-only analysis → reports/llm_advisory_*.md (Phase 1 safe)
  disabled           : no-op
  apply              : legacy auto config.yaml rewrite (Phase 2+ only, gated)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMOptimizer")

INTERVAL_SEC = int(os.getenv("LLM_INTERVAL_SEC", str(24 * 60 * 60)))  # daily by default
MODE = os.getenv("LLM_MODE", "advisor").lower()
PHASE1_SAFE = os.getenv("LLM_PHASE1_SAFE", "true").lower() == "true"


async def _run_apply_mode() -> None:
    if PHASE1_SAFE:
        logger.error(
            "LLM_MODE=apply blocked: LLM_PHASE1_SAFE=true (Phase 1). "
            "Use advisor mode or set LLM_PHASE1_SAFE=false after gate exit."
        )
        return

    allow = os.getenv("LLM_ALLOW_CONFIG_WRITE", "false").lower() == "true"
    if not allow:
        logger.error("LLM_MODE=apply requires LLM_ALLOW_CONFIG_WRITE=true")
        return

    # Legacy path — import only when explicitly enabled
    from legacy_apply import optimize_config

    await optimize_config()


async def _run_advisor_mode() -> None:
    from advisor import run_advisory

    path = await run_advisory()
    if path:
        logger.info("Advisor run complete: %s", path)


async def main() -> None:
    logger.info("LLM Optimizer started — mode=%s phase1_safe=%s", MODE, PHASE1_SAFE)
    await asyncio.sleep(int(os.getenv("LLM_BOOT_DELAY_SEC", "60")))

    while True:
        if MODE == "disabled":
            logger.debug("LLM_MODE=disabled — sleeping")
        elif MODE == "advisor":
            try:
                await _run_advisor_mode()
            except Exception as exc:
                logger.error("Advisor run failed: %s", exc)
        elif MODE == "apply":
            try:
                await _run_apply_mode()
            except Exception as exc:
                logger.error("Apply run failed: %s", exc)
        else:
            logger.error("Unknown LLM_MODE=%r — use advisor|disabled|apply", MODE)

        logger.info("Next LLM run in %.1f hours", INTERVAL_SEC / 3600)
        await asyncio.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
