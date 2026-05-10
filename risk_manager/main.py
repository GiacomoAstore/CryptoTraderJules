import asyncio
import json
import logging
import os
import time
import uuid
from typing import Dict, Optional
from collections import deque
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskManager")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Risk Parameters
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 3))
MAX_EXPOSURE_PER_SYMBOL_USDT = float(os.getenv("MAX_EXPOSURE_PER_SYMBOL_USDT", 1000.0))
MAX_DAILY_LOSS_USDT = float(os.getenv("MAX_DAILY_LOSS_USDT", 50.0))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 5))
CONSECUTIVE_LOSS_PAUSE_MINUTES = int(os.getenv("CONSECUTIVE_LOSS_PAUSE_MINUTES", 15))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", 0.02)) # 2% risk per trade
STOP_LOSS_ATR_MULTIPLIER = float(os.getenv("STOP_LOSS_ATR_MULTIPLIER", 1.5))
TAKE_PROFIT_ATR_MULTIPLIER = float(os.getenv("TAKE_PROFIT_ATR_MULTIPLIER", 3.0))

class RiskManager:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.tick_history: Dict[str, deque] = {} # For ATR
        self.open_positions: Dict[str, dict] = {} 
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        
    async def get_paper_balance(self) -> float:
        val = await self.redis_client.get("paper:balance")
        return float(val) if val else float(os.getenv("STARTING_CAPITAL", 100.0))

    def _calculate_atr(self, symbol: str) -> float:
        history = self.tick_history.get(symbol, [])
        if len(history) < 2:
            return 0.0
        
        true_ranges = []
        for i in range(1, len(history)):
            high = max(history[i]["price"], history[i-1]["price"])
            low = min(history[i]["price"], history[i-1]["price"])
            true_ranges.append(high - low)
            
        return sum(true_ranges) / len(true_ranges)

    async def update_tick(self, tick: dict):
        symbol = tick["symbol"]
        if symbol not in self.tick_history:
            self.tick_history[symbol] = deque(maxlen=14)
        self.tick_history[symbol].append(tick)

    async def check_circuit_breaker(self) -> bool:
        cb_state = await self.redis_client.hgetall("risk:circuit_breaker")
        if cb_state and cb_state.get("status") == "open":
            until = float(cb_state.get("until", 0))
            if time.time() < until:
                return True
            else:
                logger.info("Circuit breaker expired. Resuming trading.")
                await self.redis_client.hset("risk:circuit_breaker", "status", "closed")
                return False
        return False

    async def trigger_circuit_breaker(self, reason: str, duration_minutes: int = 1440): # Default to midnight (roughly 24h)
        until = time.time() + (duration_minutes * 60)
        await self.redis_client.hset("risk:circuit_breaker", mapping={
            "status": "open",
            "reason": reason,
            "until": until,
            "timestamp": time.time()
        })
        logger.error(f"CIRCUIT BREAKER OPENED: {reason}. Paused until {until}")
        # Send alert
        await self.redis_client.publish("alerts:telegram", json.dumps({
            "event": "circuit_breaker",
            "message": f"🚨 CIRCUIT BREAKER OPENED\nReason: {reason}\nPaused for {duration_minutes} minutes."
        }))

    async def process_signal(self, signal: dict):
        if await self.check_circuit_breaker():
            logger.warning("Signal rejected: Circuit breaker is OPEN.")
            return

        symbol = signal["symbol"]
        price = signal["price"]
        direction = signal["type"]

        # Control 1: Max Open Positions
        if len(self.open_positions) >= MAX_OPEN_POSITIONS and symbol not in self.open_positions:
            logger.warning(f"Signal rejected: Max open positions reached ({MAX_OPEN_POSITIONS})")
            return

        # ATR Calculation
        atr = self._calculate_atr(symbol)
        if atr == 0:
            atr = price * 0.01 # Fallback to 1%

        # Position Sizing
        balance = await self.get_paper_balance()
        risk_amount = balance * RISK_PER_TRADE_PCT
        # Size = Risk Amount / Stop Loss Distance
        sl_distance = atr * STOP_LOSS_ATR_MULTIPLIER
        qty = risk_amount / sl_distance

        # Control 2: Max Exposure
        exposure = qty * price
        if exposure > MAX_EXPOSURE_PER_SYMBOL_USDT:
            qty = MAX_EXPOSURE_PER_SYMBOL_USDT / price
            logger.info(f"Capped {symbol} exposure to {MAX_EXPOSURE_PER_SYMBOL_USDT} USDT")

        # Calculate SL/TP
        if direction == "BUY":
            sl_price = price - (atr * STOP_LOSS_ATR_MULTIPLIER)
            tp_price = price + (atr * TAKE_PROFIT_ATR_MULTIPLIER)
        else:
            sl_price = price + (atr * STOP_LOSS_ATR_MULTIPLIER)
            tp_price = price - (atr * TAKE_PROFIT_ATR_MULTIPLIER)

        command = {
            "command_id": str(uuid.uuid4()),
            "type": direction,
            "symbol": symbol,
            "price": price,
            "quantity": qty,
            "stop_loss_price": sl_price,
            "take_profit_price": tp_price,
            "timestamp_ms": int(time.time() * 1000),
            "strategy": signal.get("strategy_name", "Unknown")
        }

        # Track tentatively
        self.open_positions[symbol] = command

        logger.info(f"Signal APPROVED: {command['type']} {command['symbol']} Qty: {qty:.4f} @ {price} (SL: {sl_price:.2f}, TP: {tp_price:.2f})")
        await self.redis_client.publish(f"approved_orders:{symbol}", json.dumps(command))
        # Legacy support
        await self.redis_client.publish("approved_orders", json.dumps(command))

    async def handle_trade_execution(self, trade: dict):
        symbol = trade.get("symbol")
        pnl = float(trade.get("pnl_usdt", 0))
        
        if symbol in self.open_positions:
            del self.open_positions[symbol]

        self.daily_pnl += pnl
        
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Control 3: Daily Drawdown
        if self.daily_pnl < -MAX_DAILY_LOSS_USDT:
            await self.trigger_circuit_breaker(f"Max Daily Loss Reached ({self.daily_pnl:.2f} USDT)")
            self.daily_pnl = 0 # Reset for next cycle
            self.consecutive_losses = 0

        # Control 4: Consecutive Losses
        elif self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            await self.trigger_circuit_breaker(f"{MAX_CONSECUTIVE_LOSSES} Consecutive Losses", CONSECUTIVE_LOSS_PAUSE_MINUTES)
            self.consecutive_losses = 0

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("signals:*", "ticks:*", "executed_trades")

    rm = RiskManager(redis_client)
    logger.info("Risk Manager started. Listening...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            data = json.loads(message["data"])

            if channel.startswith("ticks:"):
                await rm.update_tick(data)
            elif channel.startswith("signals:"):
                await rm.process_signal(data)
            elif channel == "executed_trades":
                await rm.handle_trade_execution(data)

if __name__ == "__main__":
    asyncio.run(main())
