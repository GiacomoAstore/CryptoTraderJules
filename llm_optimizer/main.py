import asyncio
import os
import logging
import asyncpg
import yaml
from openai import AsyncOpenAI
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMOptimizer")

DB_DSN = f"postgresql://{os.getenv('DB_USER', 'crypto_user')}:{os.getenv('DB_PASSWORD', 'crypto_pass')}@{os.getenv('DB_HOST', 'timescaledb')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'cryptoscalper_db')}"
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
SHARED_CONFIG_PATH = "/app/shared_config/config.yaml"

# Esecuzione ogni 4 ore (in secondi) per risparmiare token
OPTIMIZATION_INTERVAL = 4 * 60 * 60

SYSTEM_PROMPT = """You are an expert algorithmic trading quantitative analyst. 
Your task is to analyze the recent performance metrics of a high-frequency trading bot and output a new, optimized `config.yaml` file for the signal engine.

Rules:
1. ONLY return the valid YAML code. Do not wrap it in markdown code blocks (e.g. no ```yaml). Do not output any conversational text.
2. If a strategy has a terrible win rate (e.g., < 40%) or high loss, consider reducing its weight or disabling it.
3. If a strategy is highly profitable, increase its weight slightly.
4. Keep the 'consensus' threshold reasonable compared to the sum of active weights.
"""

async def fetch_performance_metrics():
    try:
        pool = await asyncpg.create_pool(dsn=DB_DSN)
        # We assume recent trades to calculate win rate per strategy.
        # This is a simplified query; in production, we would calculate actual PnL.
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT strategy_name as strategy, 
                       COUNT(*) as total_trades,
                       COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) as wins
                FROM trades 
                GROUP BY strategy_name
            ''')
            
            metrics = {}
            for row in rows:
                total = row['total_trades']
                wins = row['wins']
                win_rate = (wins / total) if total > 0 else 0
                metrics[row['strategy']] = {
                    'total_trades': total,
                    'win_rate': round(win_rate, 2)
                }
            
            await pool.close()
            return metrics
    except Exception as e:
        logger.error(f"Failed to fetch metrics: {e}")
        return {}

async def optimize_config():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key":
        logger.warning("Groq API Key not set. Skipping optimization.")
        return

    metrics = await fetch_performance_metrics()
    
    if not os.path.exists(SHARED_CONFIG_PATH):
        logger.warning(f"Config file not found at {SHARED_CONFIG_PATH}. Skipping.")
        return

    with open(SHARED_CONFIG_PATH, "r") as f:
        current_config_str = f.read()

    logger.info("Calling Groq Llama 3 70B API...")
    # Groq uses the OpenAI SDK format
    client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    
    prompt = f"Current Metrics:\n{metrics}\n\nCurrent config.yaml:\n{current_config_str}\n\nPlease provide the optimized YAML."

    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        )
        
        new_yaml = response.choices[0].message.content.strip()
        
        # Remove markdown if the LLM hallucinated it
        if new_yaml.startswith("```yaml"):
            new_yaml = new_yaml.split("```yaml")[1]
        if new_yaml.startswith("```"):
            new_yaml = new_yaml.split("```")[1]
        if new_yaml.endswith("```"):
            new_yaml = new_yaml.rsplit("```", 1)[0]
            
        new_yaml = new_yaml.strip()

        # Validate YAML
        yaml.safe_load(new_yaml)
        
        # Save to file
        with open(SHARED_CONFIG_PATH, "w") as f:
            f.write(new_yaml)
        
        logger.info("Successfully updated config.yaml.")
        
        # Trigger Hot Reload
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        await redis_client.publish("system:commands", "RELOAD_CONFIG")
        logger.info("Published RELOAD_CONFIG command to Redis.")

    except Exception as e:
        logger.error(f"Optimization failed: {e}")

async def main():
    logger.info("LLM Optimizer Agent Started.")
    # Wait for the system to boot up fully before starting the loop
    await asyncio.sleep(60)
    
    while True:
        await optimize_config()
        logger.info(f"Sleeping for {OPTIMIZATION_INTERVAL / 3600} hours...")
        await asyncio.sleep(OPTIMIZATION_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
