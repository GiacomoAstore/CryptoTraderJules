import asyncio
import json
import logging
import os
import re
import httpx
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TelegramAlerter")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

PHASE1_QUIET = os.getenv("PHASE1_QUIET", "true").lower() == "true"

MDV2_ESCAPE_CHARS = r"\_*\[\]()~`>#+\-=|{}.!"


def escape_markdown(text: str) -> str:
    return re.sub(r"([\_*\[\]()~`>#+\-=|{}.!])", r"\\\1", str(text))


async def send_telegram_message(text: str, *, plain: bool = False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram config missing. Cannot send alert.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if not plain:
        payload["parse_mode"] = "MarkdownV2"

    async with httpx.AsyncClient() as client:
        backoff = 1
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.post(url, json=payload, timeout=10.0)
                if response.status_code == 200:
                    logger.info("Message sent to Telegram.")
                    return
                elif response.status_code == 429:
                    retry_after = response.json().get("parameters", {}).get("retry_after", backoff)
                    logger.warning(f"Rate limited. Retry in {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    backoff = min(60, backoff * 2)
                else:
                    logger.error(f"Telegram error: {response.status_code} - {response.text}")
                    # If it's a client error (e.g. bad formatting, wrong token/chat_id), abort.
                    if 400 <= response.status_code < 500:
                        return
                    await asyncio.sleep(backoff)
                    backoff = min(60, backoff * 2)
            except Exception as e:
                logger.error(f"Network error (attempt {attempt}): {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)


async def format_and_send_trade(trade_data: dict):
    if PHASE1_QUIET:
        return
    symbol = trade_data.get("symbol", "UNKNOWN")
    side = trade_data.get("side", "UNKNOWN")
    entry = trade_data.get("entry_price", 0.0)
    exit_p = trade_data.get("exit_price", 0.0)
    qty = trade_data.get("quantity", 0.0)
    pnl_usdt = trade_data.get("pnl_usdt", 0.0)
    pnl_pct = trade_data.get("pnl_pct", 0.0)
    reason = trade_data.get("close_reason", "UNKNOWN")
    emoji = "📈" if float(pnl_usdt) > 0 else "📉"
    variant = trade_data.get("ab_variant", "A")
    strategy_name = trade_data.get("strategy_name", "UNKNOWN")
    text = (
        f"{emoji} *TRADE CLOSED [{escape_markdown(variant)}]* {emoji}\n\n"
        f"🏷 *Symbol:* {escape_markdown(symbol)}\n"
        f"🧠 *Strategy:* {escape_markdown(strategy_name)}\n"
        f"🔄 *Side:* {escape_markdown(side)}\n"
        f"💵 *PnL:* {escape_markdown(str(round(float(pnl_usdt), 2)))} USDT\n"
        f"ℹ️ *Reason:* {escape_markdown(reason)}"
    )
    await send_telegram_message(text)


def _allowed_phase1_event(event: str) -> bool:
    allowed = {
        "phase1_morning",
        "phase1_urgent",
        "circuit_breaker",
        "Daily Report",
    }
    if event.startswith("phase1_"):
        return True
    return event in allowed


_URGENT_PREFIX = {
    "circuit_breaker_open": "CIRCUIT BREAKER APERTO",
    "stack_restart": "RESTART NON PIANIFICATO",
    "strategy_pf_alert": "STRATEGIA SOTTO SOGLIA",
}


async def format_and_send_alert(alert_data: dict, *, plain: bool = False):
    event = alert_data.get("event", "ALERT")
    msg = alert_data.get("message", "")

    if PHASE1_QUIET and not _allowed_phase1_event(event):
        logger.debug("Skipping non-phase1 alert: %s", event)
        return

    if plain or event.startswith("phase1_") or event in _URGENT_PREFIX:
        prefix = _URGENT_PREFIX.get(event)
        body = f"{prefix}\n\n{msg}" if prefix and not msg.startswith(prefix) else msg
        await send_telegram_message(body, plain=True)
        return

    text = f"🚨 *SYSTEM ALERT: {escape_markdown(event.upper())}* 🚨\n\n{escape_markdown(msg)}"
    await send_telegram_message(text)


async def main():
    logger.info(
        "Telegram Alerter started (PHASE1_QUIET=%s — trades silenced, morning+urgent only)",
        PHASE1_QUIET,
    )
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("executed_trades", "alerts:telegram", "phase1:urgent")

    async for message in pubsub.listen():
        if message["type"] not in ("message", "pmessage"):
            continue
        channel = message["channel"]
        try:
            data = json.loads(message["data"])
        except Exception as e:
            logger.error(f"JSON parse error on {channel}: {e}")
            continue

        if channel == "executed_trades":
            asyncio.create_task(format_and_send_trade(data))
        elif channel in ("alerts:telegram", "phase1:urgent"):
            plain = channel == "phase1:urgent" or str(data.get("event", "")).startswith("phase1_")
            asyncio.create_task(format_and_send_alert(data, plain=plain))


if __name__ == "__main__":
    asyncio.run(main())
