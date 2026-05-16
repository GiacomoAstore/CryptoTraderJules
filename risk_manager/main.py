import asyncio
import json
import logging
import os
import time
import uuid
import asyncpg
from typing import Dict, Optional
from collections import deque
from decimal import Decimal, getcontext
import redis.asyncio as redis

# Imposta la precisione per i calcoli finanziari
getcontext().prec = 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RiskManager")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Risk Parameters - Convertiti in Decimal per coerenza
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 3))
MAX_EXPOSURE_PER_SYMBOL_USDT = Decimal(os.getenv("MAX_EXPOSURE_PER_SYMBOL_USDT", "1000.0"))
MAX_DAILY_LOSS_USDT = Decimal(os.getenv("MAX_DAILY_LOSS_USDT", "50.0"))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 5))
CONSECUTIVE_LOSS_PAUSE_MINUTES = int(os.getenv("CONSECUTIVE_LOSS_PAUSE_MINUTES", 15))
RISK_PER_TRADE_PCT = Decimal(os.getenv("RISK_PER_TRADE_PCT", "0.02"))
STOP_LOSS_ATR_MULTIPLIER = Decimal(os.getenv("STOP_LOSS_ATR_MULTIPLIER", "1.5"))
TAKE_PROFIT_ATR_MULTIPLIER = Decimal(os.getenv("TAKE_PROFIT_ATR_MULTIPLIER", "3.0"))

class RiskManager:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.tick_history: Dict[str, deque] = {} # For ATR
        self.open_positions: Dict[str, dict] = {} 
        
    async def get_paper_balance(self, variant: str = "A") -> Decimal:
        val = await self.redis_client.get(f"paper:balance:{variant}")
        if not val:
            # Fallback se la chiave specifica non esiste
            val = await self.redis_client.get("paper:balance")
        return Decimal(val) if val else Decimal(os.getenv("STARTING_CAPITAL", "100.0"))

    async def get_daily_metrics(self):
        """Recupera PnL e perdite consecutive da Redis per garantire persistenza tra riavvii."""
        today = time.strftime("%Y-%m-%d")
        pnl = await self.redis_client.get(f"risk:daily_pnl:{today}")
        losses = await self.redis_client.get(f"risk:consecutive_losses:{today}")
        return Decimal(pnl or "0"), int(losses or 0)

    async def update_daily_metrics(self, pnl_delta: Decimal, is_loss: bool):
        today = time.strftime("%Y-%m-%d")
        current_pnl, current_losses = await self.get_daily_metrics()
        
        new_pnl = current_pnl + pnl_delta
        new_losses = current_losses + 1 if is_loss else 0
        
        # Salvataggio su Redis con scadenza 48h per cleanup automatico
        await self.redis_client.setex(f"risk:daily_pnl:{today}", 172800, str(new_pnl))
        await self.redis_client.setex(f"risk:consecutive_losses:{today}", 172800, str(new_losses))
        return new_pnl, new_losses

    def _calculate_atr(self, symbol: str) -> Decimal:
        history = self.tick_history.get(symbol, [])
        if len(history) < 2:
            return Decimal("0")
        
        true_ranges = []
        for i in range(1, len(history)):
            p1 = Decimal(str(history[i]["price"]))
            p0 = Decimal(str(history[i-1]["price"]))
            high = max(p1, p0)
            low = min(p1, p0)
            true_ranges.append(high - low)
            
        raw_atr = sum(true_ranges) / len(true_ranges)
        last_price = Decimal(str(history[-1]["price"]))
        
        # FLOOR DI SICUREZZA: L'ATR non può essere inferiore allo 0.15% del prezzo
        # Questo protegge contro Stop Loss che verrebbero mangiati dallo spread + commissioni
        min_atr = last_price * Decimal("0.0015") 
        
        final_atr = max(raw_atr, min_atr)
        return final_atr

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

    async def trigger_circuit_breaker(self, reason: str, duration_minutes: int = 1440):
        until = time.time() + (duration_minutes * 60)
        await self.redis_client.hset("risk:circuit_breaker", mapping={
            "status": "open",
            "reason": reason,
            "until": str(until),
            "timestamp": str(time.time())
        })
        logger.error(f"CIRCUIT BREAKER OPENED: {reason}. Paused until {until}")
        await self.redis_client.publish("alerts:telegram", json.dumps({
            "event": "circuit_breaker",
            "message": f"🚨 CIRCUIT BREAKER OPENED\nReason: {reason}\nPaused for {duration_minutes} minutes."
        }))

    async def process_signal(self, signal: dict):
        if await self.check_circuit_breaker():
            logger.warning("Signal rejected: Circuit breaker is OPEN.")
            return

        symbol = signal["symbol"]
        price = Decimal(str(signal["price"]))
        direction = signal["type"]

        pos_key = f"{symbol}_{signal.get('ab_variant', 'A')}"
        if len(self.open_positions) >= MAX_OPEN_POSITIONS and pos_key not in self.open_positions:
            logger.warning(f"Signal rejected: Max open positions reached ({MAX_OPEN_POSITIONS})")
            return

        atr = self._calculate_atr(symbol)
        if atr == 0:
            atr = price * Decimal("0.01")

        balance = await self.get_paper_balance(signal.get("ab_variant", "A"))
        risk_amount = balance * RISK_PER_TRADE_PCT
        sl_distance = atr * STOP_LOSS_ATR_MULTIPLIER
        
        if sl_distance == 0: # Avoid division by zero
            return
            
        qty = risk_amount / sl_distance

        exposure = qty * price
        if exposure > MAX_EXPOSURE_PER_SYMBOL_USDT:
            qty = MAX_EXPOSURE_PER_SYMBOL_USDT / price
            logger.info(f"Capped {symbol} exposure to {MAX_EXPOSURE_PER_SYMBOL_USDT} USDT")

        # Caricamento parametri dinamici da Redis/Config se necessario
        # Per ora usiamo quelli globali aggiornati nel file
        COMMISSION_RATE = Decimal(os.getenv("COMMISSION_RATE", "0.001"))
        MIN_PROFIT_MULTIPLIER = Decimal(os.getenv("MIN_PROFIT_MULTIPLIER_VS_FEES", "3.0"))

        if direction == "BUY":
            sl_price = price - (atr * STOP_LOSS_ATR_MULTIPLIER)
            tp_price = price + (atr * TAKE_PROFIT_ATR_MULTIPLIER)
        else:
            sl_price = price + (atr * STOP_LOSS_ATR_MULTIPLIER)
            tp_price = price - (atr * TAKE_PROFIT_ATR_MULTIPLIER)

        # CHECK REDDITIVITA' VS COMMISSIONI (Fee Burn Protection)
        expected_gross_profit = abs(tp_price - price) * qty
        total_commissions = (price * qty * COMMISSION_RATE) + (tp_price * qty * COMMISSION_RATE)
        
        if expected_gross_profit < (total_commissions * MIN_PROFIT_MULTIPLIER):
            msg = f"🚫 Signal REJECTED: {symbol} {direction}\nReason: Low Profitability\nExp. Profit: ${expected_gross_profit:.4f}\nTotal Fees: ${total_commissions:.4f}"
            logger.warning(msg)
            await self.redis_client.publish("alerts:telegram", json.dumps({
                "event": "risk_filter",
                "message": msg
            }))
            return

        command = {
            "command_id": str(uuid.uuid4()),
            "type": direction,
            "symbol": symbol,
            "price": str(price),
            "quantity": str(qty),
            "stop_loss_price": str(sl_price),
            "take_profit_price": str(tp_price),
            "timestamp_ms": int(time.time() * 1000),
            "strategy": signal.get("strategy_name", "Unknown"),
            "ab_variant": signal.get("ab_variant", "A")
        }

        pos_key = f"{symbol}_{signal.get('ab_variant', 'A')}"
        self.open_positions[pos_key] = command

        logger.info(f"Signal APPROVED [{command['ab_variant']}]: {command['type']} {command['symbol']} Qty: {qty:.4f} @ {price} (SL: {sl_price:.2f}, TP: {tp_price:.2f})")
        await self.redis_client.publish(f"approved_orders:{symbol}", json.dumps(command))
        await self.redis_client.publish("approved_orders", json.dumps(command))

    async def handle_trade_execution(self, trade: dict):
        pos_key = f"{symbol}_{trade.get('ab_variant', 'A')}"
        if pos_key in self.open_positions:
            del self.open_positions[pos_key]

        new_pnl, new_losses = await self.update_daily_metrics(pnl, pnl < 0)

        if new_pnl < -MAX_DAILY_LOSS_USDT:
            await self.trigger_circuit_breaker(f"Max Daily Loss Reached ({new_pnl:.2f} USDT)")
            # Reset non necessario perché legato alla data su Redis
        elif new_losses >= MAX_CONSECUTIVE_LOSSES:
            await self.trigger_circuit_breaker(f"{MAX_CONSECUTIVE_LOSSES} Consecutive Losses", CONSECUTIVE_LOSS_PAUSE_MINUTES)

async def reconciliation_loop(rm: RiskManager):
    """
    Loop periodico che verifica la consistenza tra memoria locale e Database.
    In produzione, questo loop interrogherebbe anche le API dell'exchange (es. Binance).
    """
    logger.info("Reconciliation loop started.")
    # Poiché RiskManager non ha un pool DB diretto, usiamo asyncpg qui o passiamo un riferimento.
    # Per semplicità in questa fase, usiamo la connessione Redis per check di heartbeat.
    # In una versione più avanzata, RiskManager interroga TimescaleDB direttamente.
    
    DB_DSN = f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:{os.getenv('DB_PASSWORD', 'crypto_pass')}@{os.getenv('DB_HOST', 'timescaledb')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'cryptoscalper_db')}"
    pool = await asyncpg.create_pool(dsn=DB_DSN)
    
    while True:
        try:
            await asyncio.sleep(60) # Ogni minuto
            
            async with pool.acquire() as conn:
                db_positions = await conn.fetch("SELECT symbol, ab_variant FROM positions")
                db_keys = {f"{r['symbol']}_{r['ab_variant']}" for r in db_positions}
                
                # Check 1: In DB ma non in memoria
                for db_key in db_keys:
                    if db_key not in rm.open_positions:
                        logger.error(f"RECONCILIATION FAILURE: Position {db_key} found in DB but missing from memory!")
                        await rm.redis_client.publish("alerts:telegram", json.dumps({
                            "event": "reconciliation_error",
                            "message": f"⚠️ RECONCILIATION ERROR\nPosition {db_key} is in DB but missing from RiskManager state.\nPotential logic desync!"
                        }))
                        
                # Check 2: In memoria ma non in DB (estremamente critico)
                for mem_key in rm.open_positions.keys():
                    if mem_key not in db_keys:
                        logger.error(f"RECONCILIATION FAILURE: Position {mem_key} in memory but NOT in DB!")
                        # In questo caso, la persistenza è fallita.
                
            # Check 3: Data Ingestion Heartbeat
            hb_str = await rm.redis_client.get("ingestion:heartbeat")
            if hb_str:
                age = (time.time() * 1000) - int(hb_str)
                if age > 10000: # 10 secondi
                    logger.warning(f"STALE DATA DETECTED: Last heartbeat {age}ms ago. Entering SAFE MODE.")
                    await rm.redis_client.set("bot:safe_mode", "1")
                else:
                    await rm.redis_client.delete("bot:safe_mode")
                    
        except Exception as e:
            logger.error(f"Reconciliation loop error: {e}")
            await asyncio.sleep(10)

async def main():
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()

    await pubsub.psubscribe("signals:*", "ticks:*", "executed_trades")

    rm = RiskManager(redis_client)
    
    # Avvia reconciliation loop in background
    asyncio.create_task(reconciliation_loop(rm))
    
    logger.info("Risk Manager started. Listening...")

    async for message in pubsub.listen():
        if message["type"] in ["message", "pmessage"]:
            channel = message["channel"]
            try:
                data = json.loads(message["data"])
                if channel.startswith("ticks:"):
                    await rm.update_tick(data)
                elif channel.startswith("signals:"):
                    # Check Safe Mode
                    safe_mode = await redis_client.get("bot:safe_mode")
                    if safe_mode:
                        logger.warning("Signal rejected: Bot is in SAFE MODE due to stale data.")
                        continue
                    await rm.process_signal(data)
                elif channel == "executed_trades":
                    await rm.handle_trade_execution(data)
            except Exception as e:
                logger.error(f"Error processing message from {channel}: {e}")

if __name__ == "__main__":
    asyncio.run(main())


