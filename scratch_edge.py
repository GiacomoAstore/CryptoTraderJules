import asyncio
import asyncpg
from collections import deque
import time

DB_DSN = "postgresql://crypto_user:crypto_pass@timescaledb:5432/cryptoscalper_db"

async def analyze_symbol(pool, symbol: str, hours: int = 12):
    print(f"\nAnalyzing {symbol} over the last {hours} hours...")
    
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT time, price, extract(epoch from time)*1000 as ts_ms 
            FROM ticks 
            WHERE symbol = $1 
            AND time >= NOW() - interval '{hours} hours'
            ORDER BY time ASC
        """, symbol)
        
    if not rows:
        print("No data found.")
        return

    print(f"Loaded {len(rows)} ticks.")
    
    fast_period = 8
    slow_period = 21
    min_separation_bps = 3.0
    momentum_window_ms = 3500
    momentum_threshold_bps = 15.0
    trailing_stop_bps = 18.0
    target_bps = 40.0
    max_tracking_time_ms = 30 * 60 * 1000 # 30 minutes
    
    prev_fast = None
    prev_slow = None
    recent_ticks = deque()
    signals = []
    
    for row in rows:
        price = float(row['price'])
        ts_ms = int(row['ts_ms'])
        
        recent_ticks.append((ts_ms, price))
        while recent_ticks and recent_ticks[0][0] < ts_ms - 4000:
            recent_ticks.popleft()
            
        for sig in signals:
            if sig['status'] == 'completed':
                continue
                
            if ts_ms - sig['entry_ts'] > max_tracking_time_ms:
                sig['status'] = 'completed'
                continue
                
            if price > sig['peak_price']:
                sig['peak_price'] = price
                
            # Track MAE (Maximum Adverse Excursion) = minimum price reached
            if price < sig['min_price']:
                sig['min_price'] = price
                
            if not sig['stopped_out']:
                if price <= sig['peak_price'] * (1 - trailing_stop_bps / 10000.0):
                    sig['stopped_out'] = True
                    sig['stopped_out_ts'] = ts_ms
                    
            if price >= sig['entry_price'] * (1 + target_bps / 10000.0):
                sig['target_hit'] = True
                sig['target_hit_ts'] = ts_ms
                # Record the max pullback BEFORE hitting target
                pullback_bps = (sig['entry_price'] - sig['min_price']) / sig['entry_price'] * 10000
                sig['max_pullback_bps'] = max(0, pullback_bps)
                sig['status'] = 'completed'
                
        if prev_fast is None:
            prev_fast = price
            prev_slow = price
        else:
            prev_fast = (price - prev_fast) * (2 / (fast_period + 1)) + prev_fast
            prev_slow = (price - prev_slow) * (2 / (slow_period + 1)) + prev_slow
            
        if prev_fast > prev_slow:
            sep_bps = (prev_fast - prev_slow) / price * 10000
            if sep_bps >= min_separation_bps:
                ref_price = None
                for t, p in recent_ticks:
                    if ts_ms - t <= momentum_window_ms:
                        ref_price = p
                        break
                
                if ref_price and ref_price > 0:
                    change_bps = (price - ref_price) / ref_price * 10000
                    if change_bps >= momentum_threshold_bps:
                        if not signals or (ts_ms - signals[-1]['entry_ts'] > 5000):
                            signals.append({
                                'entry_price': price,
                                'entry_ts': ts_ms,
                                'peak_price': price,
                                'min_price': price,
                                'stopped_out': False,
                                'stopped_out_ts': None,
                                'target_hit': False,
                                'target_hit_ts': None,
                                'status': 'active'
                            })
                            
    stopped_then_target = [s for s in signals if s['stopped_out'] and s['target_hit']]
    
    if stopped_then_target:
        pullbacks = [s['max_pullback_bps'] for s in stopped_then_target]
        avg_pb = sum(pullbacks) / len(pullbacks)
        max_pb = max(pullbacks)
        min_pb = min(pullbacks)
        print(f"\nStats for {len(pullbacks)} successful continuation trades:")
        print(f"Average Pullback: {avg_pb:.2f} bps")
        print(f"Max Pullback: {max_pb:.2f} bps")
        print(f"Min Pullback: {min_pb:.2f} bps")

async def main():
    pool = await asyncpg.create_pool(DB_DSN)
    await analyze_symbol(pool, "ETHUSDT", 24)
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
