import asyncio
import asyncpg
from decimal import Decimal

DB_DSN = "postgresql://crypto_user:crypto_pass@timescaledb:5432/cryptoscalper_db"

async def analyze_duration():
    print("Connecting to DB...")
    conn = await asyncpg.connect(DB_DSN)
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    for symbol in symbols:
        print(f"\nAnalyzing {symbol} Holding Times...")
        
        # Fetch a smaller set of recent ticks to simulate entries
        query = """
            SELECT price, timestamp_ms 
            FROM ticks 
            WHERE symbol = $1 AND price > 0
            ORDER BY timestamp_ms ASC
            LIMIT 50000
        """
        rows = await conn.fetch(query, symbol)
        
        if len(rows) < 1000:
            print(f"Not enough data for {symbol}")
            continue
            
        prices = [float(row["price"]) for row in rows]
        timestamps = [int(row["timestamp_ms"]) for row in rows]
        
        time_span_min = (timestamps[-1] - timestamps[0]) / 60000.0
        print(f"Time span for 50,000 ticks: {time_span_min:.1f} minutes")
        
        tp_bps = 52.5
        sl_bps = 27.0
        
        hit_tp = 0
        hit_sl = 0
        total_resolved = 0
        durations = []
        
        # We will sample 1 out of every 100 ticks as a hypothetical "entry"
        # to see how long it takes to hit TP or SL
        for i in range(0, len(prices) - 100, 100):
            entry_price = prices[i]
            entry_time = timestamps[i]
            
            tp_price = entry_price * (1 + tp_bps/10000)
            sl_price = entry_price * (1 - sl_bps/10000)
            
            for j in range(i+1, len(prices)):
                current_price = prices[j]
                
                # Check TP
                if current_price >= tp_price:
                    hit_tp += 1
                    durations.append(timestamps[j] - entry_time)
                    total_resolved += 1
                    break
                
                # Check SL
                if current_price <= sl_price:
                    hit_sl += 1
                    durations.append(timestamps[j] - entry_time)
                    total_resolved += 1
                    break
                    
        if total_resolved > 0:
            avg_duration_ms = sum(durations) / len(durations)
            avg_duration_sec = avg_duration_ms / 1000.0
            avg_duration_min = avg_duration_sec / 60.0
            
            print(f"Sampled {total_resolved} trades.")
            print(f"Win Rate (Hit TP first): {(hit_tp/total_resolved)*100:.2f}%")
            print(f"Loss Rate (Hit SL first): {(hit_sl/total_resolved)*100:.2f}%")
            print(f"Average Holding Time: {avg_duration_sec:.1f} seconds ({avg_duration_min:.1f} minutes)")
        else:
            print("No trades resolved within the loaded tick window.")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(analyze_duration())
