import asyncio
import asyncpg
from decimal import Decimal

DB_DSN = "postgresql://crypto_user:crypto_pass@timescaledb:5432/cryptoscalper_db"

async def analyze_atr():
    print("Connecting to DB...")
    conn = await asyncpg.connect(DB_DSN)
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    for symbol in symbols:
        print(f"\nAnalyzing {symbol}...")
        
        # Fetch the last 100,000 ticks ordered by timestamp
        query = """
            SELECT price, timestamp_ms 
            FROM ticks 
            WHERE symbol = $1 AND price > 0
            ORDER BY timestamp_ms ASC
            LIMIT 100000
        """
        rows = await conn.fetch(query, symbol)
        
        if len(rows) < 15:
            print(f"Not enough data for {symbol}: {len(rows)} ticks")
            continue
            
        print(f"Loaded {len(rows)} ticks for {symbol}.")
        
        exceed_30 = 0
        exceed_40 = 0
        exceed_50 = 0
        total_valid = 0
        max_atr_bps = 0
        
        history = []
        for row in rows:
            price = Decimal(str(row["price"]))
            history.append(price)
            if len(history) > 14:
                history.pop(0)
            
            if len(history) == 14:
                true_ranges = []
                for i in range(1, 14):
                    p1 = history[i]
                    p0 = history[i-1]
                    high = max(p1, p0)
                    low = min(p1, p0)
                    true_ranges.append(high - low)
                    
                raw_atr = sum(true_ranges) / len(true_ranges)
                atr_bps = (raw_atr / price) * 10000
                
                if atr_bps > max_atr_bps:
                    max_atr_bps = atr_bps
                
                if atr_bps >= 50:
                    exceed_50 += 1
                if atr_bps >= 40:
                    exceed_40 += 1
                if atr_bps >= 30:
                    exceed_30 += 1
                    
                total_valid += 1
                
        if total_valid > 0:
            print(f"Total 14-tick windows evaluated: {total_valid}")
            print(f"MAX ATR BPS SEEN: {max_atr_bps:.4f}")
            print(f"> 30 bps: {exceed_30} times ({(exceed_30/total_valid)*100:.2f}%)")
            print(f"> 40 bps: {exceed_40} times ({(exceed_40/total_valid)*100:.2f}%)")
            print(f"> 50 bps: {exceed_50} times ({(exceed_50/total_valid)*100:.2f}%)")
        else:
            print("No valid windows.")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(analyze_atr())
