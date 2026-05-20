"""
Phase 1 reporter — morning Telegram digest + urgent alerts only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import pytz
import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from phase1_report import build_report, format_telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Reporter")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
TZ = pytz.timezone(os.getenv("PHASE1_REPORT_TZ", "Europe/Rome"))
MORNING_HOUR = int(os.getenv("PHASE1_REPORT_HOUR", "8"))
MORNING_MINUTE = int(os.getenv("PHASE1_REPORT_MINUTE", "0"))
STRATEGY_CHECK_SEC = int(os.getenv("PHASE1_STRATEGY_CHECK_SEC", "600"))


async def send_telegram_payload(redis_client, event: str, message: str, urgent: bool = False) -> None:
    channel = "phase1:urgent" if urgent else "alerts:telegram"
    await redis_client.publish(channel, json.dumps({"event": event, "message": message}))


async def send_morning_report() -> None:
    logger.info("Building Phase 1 morning report...")
    report = await build_report(update_gate=True)
    gate = report.get("gate", {})
    day_n = int(gate.get("consecutive_clean_days", 0))
    # Day label: if gate just incremented today, show day_n; else show progress toward next
    # Morning of operational day N shows progress toward day N (after prior EOD eval)
    label = f"{max(1, day_n + 1)}/14"

    text = format_telegram(report, label)
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        await send_telegram_payload(redis_client, "phase1_morning", text, urgent=False)
        logger.info("Morning report sent to Telegram (day %s)", label)
    finally:
        await redis_client.aclose()


async def strategy_watch_loop() -> None:
    """Poll for 50-trade / PF<0.9 — immediate Telegram, no wait for morning."""
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    while True:
        try:
            report = await build_report(update_gate=False)
            for action in report.get("strategy_actions", []):
                logger.warning(action)
        except Exception as exc:
            logger.error("Strategy watch error: %s", exc)
        await asyncio.sleep(STRATEGY_CHECK_SEC)


async def manual_commands_listener() -> None:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("system:commands")
    async for message in pubsub.listen():
        if message["type"] == "message" and message["data"] == "PHASE1_MORNING_REPORT":
            await send_morning_report()


async def arm_restart_alerts() -> None:
    """After warm-up, enable unplanned-restart Telegram alerts."""
    await asyncio.sleep(int(os.getenv("PHASE1_ARM_DELAY_SEC", "300")))
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        await redis_client.set("phase1:boot_alert_armed", "1")
        logger.info("Phase 1 restart alerts armed")
    finally:
        await redis_client.aclose()


async def main() -> None:
    logger.info(
        "Phase 1 Reporter — morning %02d:%02d %s, strategy check every %ds",
        MORNING_HOUR,
        MORNING_MINUTE,
        TZ,
        STRATEGY_CHECK_SEC,
    )
    asyncio.create_task(arm_restart_alerts())

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        send_morning_report,
        "cron",
        hour=MORNING_HOUR,
        minute=MORNING_MINUTE,
    )
    scheduler.start()

    await asyncio.gather(
        strategy_watch_loop(),
        manual_commands_listener(),
    )


if __name__ == "__main__":
    asyncio.run(main())
