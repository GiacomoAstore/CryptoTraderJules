import asyncio
import json
import logging
import os
import time
import uuid
import random
from typing import Dict, Any
from decimal import Decimal, getcontext
import redis.asyncio as redis
import asyncpg

getcontext().prec = 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OrderExecutor")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
DB_DSN = f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:{os.getenv('DB_PASSWORD', 'crypto_pass')}@{os.getenv('DB_HOST', 'timescaledb')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'cryptoscalper_db')}"
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
MAX_TRADE_DURATION_SECONDS = int(os.getenv("MAX_TRADE_DURATION_SECONDS", 300))

class OrderCommand:
    def __init__(self, data: dict):
        self.command_id = data.get("command_id", str(uuid.uuid4()))
        self.symbol = data["symbol"]
        self.direction = data["type"] # BUY or SELL
        self.target_price = Decimal(str(data["price"]))
        self.quantity = Decimal(str(data["quantity"]))
        self.stop_loss = Decimal(str(data["stop_loss_price"])) if data.get("stop_loss_price") else None
        self.take_profit = Decimal(str(data["take_profit_price"])) if data.get("take_profit_price") else None
        self.strategy = data.get("strategy", "Unknown")
        self.ab_variant = data.get("ab_variant", "A")
        self.created_at = time.time()
        self.status = "PENDING"
        self.executed_price = Decimal("0")
        
    def to_dict(self):
        return {
            "command_id": self.command_id,
            "symbol": self.symbol,
            "type": self.direction,
            "price": str(self.target_price),
            "quantity": str(self.quantity),
            "stop_loss_price": str(self.stop_loss) if self.stop_loss else None,
            "take_profit_price": str(self.take_profit) if self.take_profit else None,
            "strategy": self.strategy,
            "ab_variant": self.ab_variant,
            "status": self.status,
            "executed_price": str(self.executed_price),
            "created_at": self.created_at
        }

class PaperEngine:
    def __init__(self, redis_client, db_pool):
        self.redis_client = redis_client
        self.db_pool = db_pool
        self.open_positions: Dict[str, OrderCommand] = {} # Keys: "symbol_variant"
        self.paper_balances = {
            "A": Decimal("100"),
            "B": Decimal("100")
        }

    async def init_ledger(self):
        balance_a = await self.redis_client.get("paper:balance:A")
        if balance_a:
            self.paper_balances["A"] = Decimal(balance_a)
        else:
            await self.redis_client.set("paper:balance:A", str(self.paper_balances["A"]))
            
        balance_b = await self.redis_client.get("paper:balance:B")
        if balance_b:
            self.paper_balances["B"] = Decimal(balance_b)
        else:
            await self.redis_client.set("paper:balance:B", str(self.paper_balances["B"]))

        # Caricamento posizioni aperte dal DB
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM positions")
                for row in rows:
                    cmd = OrderCommand({
                        "symbol": row["symbol"],
                        "type": row["side"],
                        "price": str(row["entry_price"]),
                        "quantity": str(row["quantity"]),
                        "stop_loss_price": str(row["stop_loss"]),
                        "take_profit_price": str(row["take_profit"]),
                        "ab_variant": row["ab_variant"]
                    })
                    cmd.status = "FILLED"
                    cmd.executed_price = row["entry_price"]
                    cmd.created_at = row["entry_time"].timestamp()
                    pos_key = f"{cmd.symbol}_{cmd.ab_variant}"
                    self.open_positions[pos_key] = cmd
                logger.info(f"Loaded {len(rows)} open positions from DB.")
        except Exception as e:
            logger.error(f"Error loading positions from DB: {e}")

    async def save_order_to_db(self, cmd: OrderCommand):
        try:
            async with self.db_pool.acquire() as conn:
                # Update orders history
                await conn.execute("""
                    INSERT INTO orders (id, symbol, side, price, quantity, status, strategy, ab_variant)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status
                """, uuid.UUID(cmd.command_id), cmd.symbol, cmd.direction, cmd.target_price, cmd.quantity, cmd.status, cmd.strategy, cmd.ab_variant)
                
                # Update active positions table
                if cmd.status == "FILLED":
                    await conn.execute("""
                        INSERT INTO positions (symbol, ab_variant, entry_time, entry_price, quantity, side, stop_loss, take_profit)
                        VALUES ($1, $2, NOW(), $3, $4, $5, $6, $7)
                        ON CONFLICT (symbol, ab_variant) DO NOTHING
                    """, cmd.symbol, cmd.ab_variant, cmd.executed_price, cmd.quantity, cmd.direction, cmd.stop_loss, cmd.take_profit)
        except Exception as e:
            logger.error(f"DB Error saving order/position: {e}")

    async def process_new_command(self, cmd_data: dict):
        cmd = OrderCommand(cmd_data)
        
        # 1. Idempotency Check (Persistent on Redis)
        lock_key = f"exec:lock:{cmd.command_id}"
        if not await self.redis_client.setnx(lock_key, "1"):
            logger.warning(f"Duplicate command ignored (Redis Lock): {cmd.command_id}")
            return
        await self.redis_client.expire(lock_key, 86400) # 24h

        # 2. Persist PENDING state
        await self.save_order_to_db(cmd)
        
        # Simula latenza di rete
        await asyncio.sleep(random.uniform(0.05, 0.15))
        
        tick_str = await self.redis_client.get(f"tick:last:{cmd.symbol.upper()}")
        if tick_str:
            tick = json.loads(tick_str)
            execute_price = Decimal(str(tick["price"]))
        else:
            execute_price = cmd.target_price

        cmd.status = "FILLED"
        cmd.executed_price = execute_price
        
        # 3. Persist FILLED state (and Active Position)
        await self.save_order_to_db(cmd)
        
        logger.info(f"PAPER EXECUTION [Variant {cmd.ab_variant}]: {cmd.direction} {cmd.quantity:.4f} {cmd.symbol} @ {cmd.executed_price:.2f}")

        pos_key = f"{cmd.symbol}_{cmd.ab_variant}"
        self.open_positions[pos_key] = cmd

    async def close_position(self, pos_key: str, close_price: Decimal, reason: str):
        if pos_key not in self.open_positions:
            return
            
        pos = self.open_positions[pos_key]
        COMMISSION_RATE = Decimal(os.getenv("COMMISSION_RATE", "0.001"))

        if pos.direction == "BUY":
            pnl_usdt = (close_price - pos.executed_price) * pos.quantity
        else:
            pnl_usdt = (pos.executed_price - close_price) * pos.quantity
            
        fee = (pos.executed_price * pos.quantity * COMMISSION_RATE) + (close_price * pos.quantity * COMMISSION_RATE)
        net_pnl = pnl_usdt - fee

        # Update paper balance persistente
        self.paper_balances[pos.ab_variant] += net_pnl
        await self.redis_client.set(f"paper:balance:{pos.ab_variant}", str(self.paper_balances[pos.ab_variant]))
        
        # Rimuovi posizione dal DB
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("DELETE FROM positions WHERE symbol = $1 AND ab_variant = $2", pos.symbol, pos.ab_variant)
        except Exception as e:
            logger.error(f"Error deleting position from DB: {e}")

        pnl_pct = (net_pnl / (pos.executed_price * pos.quantity)) * 100
        
        trade_record = {
            "symbol": pos.symbol,
            "side": pos.direction,
            "price": round(float(close_price), 4),
            "entry_price": round(float(pos.executed_price), 4),
            "exit_price": round(float(close_price), 4),
            "quantity": round(float(pos.quantity), 6),
            "pnl_usdt": round(float(net_pnl), 2),
            "pnl_pct": round(float(pnl_pct), 2),
            "close_reason": reason,
            "ab_variant": pos.ab_variant,
            "strategy_name": pos.strategy
        }
        
        await self.redis_client.publish("executed_trades", json.dumps(trade_record))
        del self.open_positions[pos_key]
        logger.info(f"POSITION CLOSED [{pos.ab_variant}]: {pos.symbol} @ {close_price} | PNL: {net_pnl:.2f} ({pnl_pct:.2f}%)")


    async def monitor_ticks(self, tick: dict):
        symbol = tick["symbol"]
        price = Decimal(str(tick["price"]))
        
        for variant in ["A", "B"]:
            pos_key = f"{symbol}_{variant}"
            if pos_key not in self.open_positions:
                continue
                
            pos = self.open_positions[pos_key]
            
            if pos.stop_loss:
                if (pos.direction == "BUY" and price <= pos.stop_loss) or \
                   (pos.direction == "SELL" and price >= pos.stop_loss):
                    await self.close_position(pos_key, price, "SL_HIT")
                    continue
                    
            if pos.take_profit:
                if (pos.direction == "BUY" and price >= pos.take_profit) or \
                   (pos.direction == "SELL" and price <= pos.take_profit):
                    await self.close_position(pos_key, price, "TP_HIT")
                    continue
                    
            if time.time() - pos.created_at > MAX_TRADE_DURATION_SECONDS:
                await self.close_position(pos_key, price, "TIMEOUT")

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    db_pool = await asyncpg.create_pool(dsn=DB_DSN)
    
    engine = PaperEngine(redis_client, db_pool)
    await engine.init_ledger()
    
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("approved_orders:*", "ticks:*", "approved_orders")
    
    logger.info("Paper Trading Engine started. Monitoring...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            try:
                data = json.loads(message["data"])
                if channel.startswith("ticks:"):
                    await engine.monitor_ticks(data)
                elif "approved_orders" in channel:
                    asyncio.create_task(engine.process_new_command(data))
            except Exception as e:
                logger.error(f"Error in main loop: {e}")

if __name__ == "__main__":
    asyncio.run(main())

