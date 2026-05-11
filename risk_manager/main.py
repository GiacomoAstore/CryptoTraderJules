import asyncio
import json
import logging
import os
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskManager")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

class RiskManager:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.open_positions = 0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0

        # Configurable thresholds
        self.MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 3))
        self.MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 5))
        self.MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", 50.0))

    async def evaluate_signal(self, signal: dict) -> bool:
        # Check global circuit breaker state in Redis
        cb_state = await self.redis.get("risk:circuit_breaker")
        if cb_state == "open":
            logger.warning("Circuit breaker is open globally. Rejecting signal.")
            return False

        if self.open_positions >= self.MAX_OPEN_POSITIONS:
            logger.info(f"Max open positions ({self.MAX_OPEN_POSITIONS}) reached. Rejecting signal.")
            return False

        if self.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            logger.warning(f"Consecutive losses limit ({self.MAX_CONSECUTIVE_LOSSES}) reached. Activating circuit breaker.")
            await self.redis.set("risk:circuit_breaker", "open")
            return False

        if self.daily_pnl <= -self.MAX_DAILY_LOSS_USDT:
            logger.warning(f"Daily loss limit reached (${self.daily_pnl}). Activating circuit breaker.")
            await self.redis.set("risk:circuit_breaker", "open")
            return False

        # Mock position tracking
        self.open_positions += 1

        return True

    async def process_trade_result(self, trade_result: dict):
        # Update metrics based on paper execution result
        self.open_positions = max(0, self.open_positions - 1)

        pnl = trade_result.get("pnl_netto", 0)
        self.daily_pnl += pnl

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("signals", "executed_trades")

    risk_manager = RiskManager(redis_client)
    logger.info("Risk Manager started. Listening for signals and trades...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message.get("channel", "")
            data = json.loads(message["data"])

            if channel == "signals":
                if data.get("is_shadow", False):
                    # Shadow signals bypass the risk manager and are sent to a separate channel
                    # for theoretical execution and evaluation (A/B testing)
                    logger.info(f"Shadow Signal detected, routing to shadow testing: {data}")
                    await redis_client.publish("shadow_orders", json.dumps(data))
                else:
                    is_approved = await risk_manager.evaluate_signal(data)
                    if is_approved:
                        logger.info(f"Signal approved by Risk Manager: {data}")
                        await redis_client.publish("approved_orders", json.dumps(data))
                    else:
                        logger.info(f"Signal rejected by Risk Manager: {data}")

            elif channel == "executed_trades":
                await risk_manager.process_trade_result(data)

if __name__ == "__main__":
    asyncio.run(main())
