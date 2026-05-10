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

# Characters that need escaping in MarkdownV2
# '_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!'
MDV2_ESCAPE_CHARS = r"\_*\[\]()~`>#+\-=|{}.!"

def escape_markdown(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2 format."""
    # We only escape if we are not manually wrapping them, but for simplicity we escape everything not explicitly formatting
    # A robust regex to escape special characters:
    return re.sub(r'([\_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

async def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram config missing. Cannot send alert.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2"
    }

    async with httpx.AsyncClient() as client:
        backoff = 1
        for attempt in range(5):
            try:
                response = await client.post(url, json=payload, timeout=10.0)
                if response.status_code == 200:
                    logger.info("Alert sent to Telegram successfully.")
                    return
                elif response.status_code == 429:
                    retry_after = response.json().get("parameters", {}).get("retry_after", backoff)
                    logger.warning(f"Rate limited by Telegram. Retrying in {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    backoff = min(60, backoff * 2)
                else:
                    logger.error(f"Failed to send alert: {response.status_code} - {response.text}")
                    return
            except Exception as e:
                logger.error(f"Network error sending telegram alert: {e}")
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

async def format_and_send_trade(trade_data: dict):
    # Data is expected from `executed_trades` channel
    symbol = trade_data.get("symbol", "UNKNOWN")
    side = trade_data.get("side", "UNKNOWN")
    entry = trade_data.get("entry_price", 0.0)
    exit_p = trade_data.get("exit_price", 0.0)
    qty = trade_data.get("quantity", 0.0)
    pnl_usdt = trade_data.get("pnl_usdt", 0.0)
    pnl_pct = trade_data.get("pnl_pct", 0.0)
    reason = trade_data.get("close_reason", "UNKNOWN")

    emoji = "📈" if pnl_usdt > 0 else "📉"
    
    text = (
        f"{emoji} *TRADE CLOSED* {emoji}\n\n"
        f"🏷 *Symbol:* {escape_markdown(symbol)}\n"
        f"🔄 *Side:* {escape_markdown(side)}\n"
        f"💰 *Entry:* {escape_markdown(str(entry))}\n"
        f"🎯 *Exit:* {escape_markdown(str(exit_p))}\n"
        f"📦 *Qty:* {escape_markdown(str(round(qty, 6)))}\n"
        f"💵 *PnL:* {escape_markdown(str(round(pnl_usdt, 2)))} USDT \\({escape_markdown(str(round(pnl_pct, 2)))}%\\)\n"
        f"ℹ️ *Reason:* {escape_markdown(reason)}"
    )
    await send_telegram_message(text)

async def format_and_send_alert(alert_data: dict):
    # Data is expected from `alerts:telegram` channel
    event = alert_data.get("event", "ALERT")
    msg = alert_data.get("message", "")
    
    text = (
        f"🚨 *SYSTEM ALERT: {escape_markdown(event.upper())}* 🚨\n\n"
        f"{escape_markdown(msg)}"
    )
    await send_telegram_message(text)

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("executed_trades", "alerts:telegram")
    logger.info("Telegram Alerter started. Listening for alerts...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            try:
                data = json.loads(message["data"])
            except Exception as e:
                logger.error(f"Failed to parse JSON message: {e}")
                continue

            if channel == "executed_trades":
                asyncio.create_task(format_and_send_trade(data))
            elif channel == "alerts:telegram":
                asyncio.create_task(format_and_send_alert(data))

if __name__ == "__main__":
    asyncio.run(main())
