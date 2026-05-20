import asyncio
import asyncpg

DB_DSN = "postgresql://crypto_user:crypto_pass@timescaledb:5432/cryptoscalper_db"

async def calculate_5m_atr():
    print("Connecting to DB...")
    conn = await asyncpg.connect(DB_DSN)
    
    symbols = ["BTCUSDT", "ETHUSDT"]
    for symbol in symbols:
        print(f"\nCalculating 5m ATR for {symbol}...")
        
        # Use time_bucket to create 5m candles directly from ticks
        # We fetch the last 48 hours of data
        query = """
            SELECT 
                time_bucket('5 minutes', to_timestamp(timestamp_ms / 1000.0)) AS time_bucket,
                (array_agg(price ORDER BY timestamp_ms ASC))[1] as open,
                MAX(price) as high,
                MIN(price) as low,
                (array_agg(price ORDER BY timestamp_ms DESC))[1] as close
            FROM ticks 
            WHERE symbol = $1 AND price > 0
            GROUP BY time_bucket
            ORDER BY time_bucket ASC
        """
        
        rows = await conn.fetch(query, symbol)
        
        if len(rows) < 15:
            print(f"Not enough 5m candles for {symbol}: {len(rows)}")
            continue
            
        print(f"Loaded {len(rows)} 5-minute candles.")
        
        # Calculate True Range
        true_ranges = []
        for i in range(1, len(rows)):
            high = float(rows[i]["high"])
            low = float(rows[i]["low"])
            prev_close = float(rows[i-1]["close"])
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            tr = max(tr1, tr2, tr3)
            true_ranges.append(tr)
            
        atr_bps_list = []
        for i in range(13, len(true_ranges)):
            window = true_ranges[i-13:i+1] # 14 periods
            atr = sum(window) / 14
            close = float(rows[i+1]["close"])
            atr_bps = (atr / close) * 10000
            atr_bps_list.append(atr_bps)
            
        if not atr_bps_list:
            continue
            
        avg_atr_bps = sum(atr_bps_list) / len(atr_bps_list)
        max_atr_bps = max(atr_bps_list)
        min_atr_bps = min(atr_bps_list)
        latest_atr_bps = atr_bps_list[-1]
        
        print(f"--- 5m ATR Stats ({len(atr_bps_list)} periods) ---")
        print(f"Average: {avg_atr_bps:.2f} bps")
        print(f"Maximum: {max_atr_bps:.2f} bps")
        print(f"Minimum: {min_atr_bps:.2f} bps")
        print(f"Latest:  {latest_atr_bps:.2f} bps")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(calculate_5m_atr())
