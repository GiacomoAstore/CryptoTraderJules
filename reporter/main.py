import asyncio
import os
import json
import logging
from datetime import datetime, date, timedelta
import pytz
import asyncpg
import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Reporter")

DB_USER = os.getenv("DB_USER", "crypto_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "crypto_pass")
DB_NAME = os.getenv("DB_NAME", "cryptoscalper_db")
DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = os.getenv("DB_PORT", "5432")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

async def get_db_pool():
    return await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        host=DB_HOST,
        port=DB_PORT
    )

async def generate_daily_report(target_date=None):
    if not target_date:
        target_date = datetime.now(pytz.utc).date()
        
    logger.info(f"Generating report for {target_date}...")
    
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pool = await get_db_pool()
    
    start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=pytz.utc)
    end_dt = start_dt + timedelta(days=1)
    
    try:
        async with pool.acquire() as conn:
            # Get trades for the day
            trades = await conn.fetch(
                """
                SELECT pnl_usdt, fee, strategy_name, open_time, close_time 
                FROM trades 
                WHERE time >= $1 AND time < $2
                """,
                start_dt, end_dt
            )
            
            if not trades:
                logger.info("No trades found for today.")
                # We can still send an empty report
                total_trades = 0
                win_count = 0
                loss_count = 0
                net_pnl = 0.0
                gross_pnl = 0.0
                win_rate = 0.0
                max_drawdown = 0.0
                sharpe = 0.0
                best_strat = "N/A"
                worst_strat = "N/A"
                avg_win = 0.0
                avg_loss = 0.0
                profit_factor = 0.0
                
            else:
                total_trades = len(trades)
                wins = [t for t in trades if t['pnl_usdt'] > 0]
                losses = [t for t in trades if t['pnl_usdt'] <= 0]
                
                win_count = len(wins)
                loss_count = len(losses)
                win_rate = win_count / total_trades if total_trades > 0 else 0
                
                net_pnl = sum(t['pnl_usdt'] for t in trades)
                fees = sum(t['fee'] or 0 for t in trades)
                gross_pnl = net_pnl + fees
                
                avg_win = sum(t['pnl_usdt'] for t in wins) / win_count if win_count > 0 else 0
                avg_loss = sum(t['pnl_usdt'] for t in losses) / loss_count if loss_count > 0 else 0
                
                profit_factor = avg_win / abs(avg_loss) if avg_loss < 0 else (999.0 if avg_win > 0 else 0.0)
                
                # Approximate Max Drawdown from PNL sequence
                cumulative = 0
                peak = 0
                max_dd_usdt = 0
                for t in trades:
                    cumulative += t['pnl_usdt']
                    if cumulative > peak:
                        peak = cumulative
                    dd = peak - cumulative
                    if dd > max_dd_usdt:
                        max_dd_usdt = dd
                
                # Starting capital for percentage calculation
                paper_balance = float(await redis_client.get("paper:balance") or 100.0)
                # We assume starting balance for the day was roughly current_balance - net_pnl
                start_balance = max(1, paper_balance - net_pnl)
                max_drawdown = (max_dd_usdt / start_balance) * 100
                
                # Sharpe Ratio Approximation (Daily Sharpe of individual trades isn't standard, but we'll use standard deviation of trades)
                if total_trades > 1:
                    mean_pnl = net_pnl / total_trades
                    variance = sum((t['pnl_usdt'] - mean_pnl)**2 for t in trades) / (total_trades - 1)
                    std_dev = math.sqrt(variance)
                    sharpe = (mean_pnl / std_dev) * math.sqrt(total_trades) if std_dev > 0 else 0
                else:
                    sharpe = 0.0
                    
                # Best/Worst Strategy
                strat_pnl = {}
                for t in trades:
                    strat_pnl[t['strategy_name']] = strat_pnl.get(t['strategy_name'], 0) + t['pnl_usdt']
                
                best_strat = max(strat_pnl.items(), key=lambda x: x[1])[0] if strat_pnl else "N/A"
                worst_strat = min(strat_pnl.items(), key=lambda x: x[1])[0] if strat_pnl else "N/A"
                
            # Upsert into daily_performance
            await conn.execute(
                """
                INSERT INTO daily_performance 
                (date, total_pnl, win_count, loss_count, win_rate, max_drawdown, sharpe_ratio)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (date) DO UPDATE SET
                total_pnl = EXCLUDED.total_pnl,
                win_count = EXCLUDED.win_count,
                loss_count = EXCLUDED.loss_count,
                win_rate = EXCLUDED.win_rate,
                max_drawdown = EXCLUDED.max_drawdown,
                sharpe_ratio = EXCLUDED.sharpe_ratio
                """,
                target_date, float(net_pnl), win_count, loss_count, float(win_rate), float(max_drawdown), float(sharpe)
            )
            logger.info("Saved to TimescaleDB.")
            
            # Save JSON Dump
            report_data = {
                "date": str(target_date),
                "total_trades": total_trades,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": win_rate,
                "gross_pnl_usdt": gross_pnl,
                "net_pnl_usdt": net_pnl,
                "avg_win_usdt": avg_win,
                "avg_loss_usdt": avg_loss,
                "profit_factor": profit_factor,
                "max_drawdown_pct": max_drawdown,
                "sharpe_ratio": sharpe,
                "best_strategy": best_strat,
                "worst_strategy": worst_strat
            }
            
            os.makedirs("/app/reports/daily", exist_ok=True)
            with open(f"/app/reports/daily/{target_date}.json", "w") as f:
                json.dump(report_data, f, indent=2)
                
            # Current Balance
            paper_balance = float(await redis_client.get("paper:balance") or 100.0)
            
            # Telegram Alert
            sign = "+" if net_pnl >= 0 else ""
            msg = f"📊 Report giornaliero — {target_date}\n"
            msg += f"💰 P&L: {sign}${net_pnl:.2f} ({sign}{(net_pnl/max(1, paper_balance-net_pnl)*100):.2f}%)\n"
            msg += f"🎯 Win rate: {(win_rate*100):.1f}% ({win_count} win / {loss_count} loss)\n"
            msg += f"📉 Max drawdown: {max_drawdown:.1f}%\n"
            msg += f"⚡ Sharpe: {sharpe:.2f}\n"
            msg += f"🏆 Migliore: {best_strat}\n"
            msg += f"🔻 Peggiore: {worst_strat}\n"
            msg += f"💼 Balance: ${paper_balance:.2f}"
            
            payload = json.dumps({"event": "Daily Report", "message": msg})
            await redis_client.publish("alerts:telegram", payload)
            logger.info("Report sent to Telegram.")
            
    except Exception as e:
        logger.error(f"Error generating report: {e}")
    finally:
        await pool.close()
        await redis_client.aclose()


async def manual_trigger_listener():
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("system:commands")
    
    logger.info("Listening for manual trigger commands...")
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = message["data"]
            if data == "GENERATE_REPORT":
                logger.info("Manual report generation triggered via Redis")
                await generate_daily_report()

async def main():
    logger.info("Starting Reporter Service...")
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(generate_daily_report, 'cron', hour=23, minute=59, timezone=pytz.utc)
    scheduler.start()
    
    await manual_trigger_listener()

if __name__ == "__main__":
    asyncio.run(main())
