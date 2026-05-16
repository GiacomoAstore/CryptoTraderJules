import asyncio
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
import asyncpg
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as redis
import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Backtester")

DB_USER = os.getenv("DB_USER", "crypto_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "crypto_pass")
DB_NAME = os.getenv("DB_NAME", "cryptoscalper_db")
DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = os.getenv("DB_PORT", "5432")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

CONFIG_PATH = "/app/shared_config/config.yaml"

async def get_db_pool():
    return await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        host=DB_HOST,
        port=DB_PORT
    )

async def walk_forward_optimization():
    logger.info("Starting Walk-Forward Optimization cycle...")
    pool = await get_db_pool()
    
    end_dt = datetime.now(pytz.utc)
    start_dt = end_dt - timedelta(days=5) # 5 days of history
    
    try:
        async with pool.acquire() as conn:
            # We would normally query ticks here.
            # E.g. ticks = await conn.fetch("SELECT * FROM ticks WHERE time >= $1", start_dt)
            # However, the current system might not be storing all ticks to TimescaleDB to save space.
            # Wait, do we have a ticks table?
            # Let's check!
            pass
            
    except Exception as e:
        logger.error(f"Error during optimization: {e}")
    finally:
        await pool.close()
        logger.info("Walk-Forward Optimization cycle completed.")

async def manual_trigger_listener():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("system:commands")
    
    logger.info("Listening for manual trigger commands...")
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = message["data"]
            if data == "RUN_OPTIMIZATION":
                logger.info("Manual optimization triggered via Redis")
                asyncio.create_task(walk_forward_optimization())

async def main():
    logger.info("Starting Backtester / Optimizer Service...")
    
    scheduler = AsyncIOScheduler()
    # Run every 48 hours
    scheduler.add_job(walk_forward_optimization, 'interval', hours=48)
    scheduler.start()
    
    await manual_trigger_listener()

if __name__ == "__main__":
    asyncio.run(main())
