#!/usr/bin/env python3
"""
Passive Market Regime Monitor for CryptoTraderJules
Evaluates historical tick/candle span and detects sustained directional trend episodes
to determine readiness for re-running the full strategy backtest.
"""
from __future__ import annotations

import asyncio
import os
import sys
import asyncpg
from datetime import datetime, timezone

def _dsn() -> str:
    user = os.getenv("DB_USER", "crypto_user")
    password = os.getenv("DB_PASSWORD", "crypto_pass")
    host = os.getenv("DB_HOST", "timescaledb")
    port = os.getenv("DB_PORT", "5432")
    db = os.getenv("DB_NAME", "cryptoscalper_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"

async def analyze_market_regime():
    conn = await asyncpg.connect(_dsn())
    
    # 1. Total Tick Span
    span_row = await conn.fetchrow("""
        SELECT MIN(time) AS min_time, MAX(time) AS max_time,
               COUNT(*) AS total_ticks,
               EXTRACT(EPOCH FROM (MAX(time) - MIN(time))) / 86400.0 AS duration_days
        FROM ticks
    """)
    
    min_t = span_row["min_time"]
    max_t = span_row["max_time"]
    days_span = float(span_row["duration_days"] or 0)
    total_ticks = int(span_row["total_ticks"] or 0)
    
    print("==================================================")
    print("      MARKET REGIME & DATA SPAN MONITOR           ")
    print("==================================================")
    print(f"Data Collection Start: {min_t.isoformat() if min_t else 'N/A'}")
    print(f"Latest Data Received:  {max_t.isoformat() if max_t else 'N/A'}")
    print(f"Total DB History Span: {days_span:.2f} days ({total_ticks:,} ticks)")
    print("--------------------------------------------------")
    
    # 2. Trend Episode Detection on Major Symbols (BTC, ETH, SOL)
    major_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    trend_episodes = []
    
    for sym in major_symbols:
        # Build 1-hour candles from ticks
        candles = await conn.fetch("""
            SELECT time_bucket('1 hour', to_timestamp(timestamp_ms / 1000.0)) AS bucket,
                   (array_agg(price ORDER BY timestamp_ms ASC))[1] AS open,
                   MAX(price) AS high, MIN(price) AS low,
                   (array_agg(price ORDER BY timestamp_ms DESC))[1] AS close
            FROM ticks
            WHERE symbol = $1 AND price > 0
            GROUP BY bucket
            ORDER BY bucket ASC
        """, sym)
        
        if len(candles) < 6:
            continue
            
        closes = [float(c["close"]) for c in candles]
        buckets = [c["bucket"] for c in candles]
        
        # Sliding 4-hour window for sustained trend
        window_size = 4
        for i in range(len(closes) - window_size):
            window_closes = closes[i : i + window_size + 1]
            net_change_pct = abs(window_closes[-1] - window_closes[0]) / window_closes[0] * 100.0
            
            # Efficiency Ratio (ER) = net move / sum of absolute 1h changes
            path_length = sum(abs(window_closes[j] - window_closes[j-1]) for j in range(1, len(window_closes)))
            er = (abs(window_closes[-1] - window_closes[0]) / path_length) if path_length > 0 else 0
            
            # Threshold: ER >= 0.50 and Net Move >= 3.5% over 4 hours
            if er >= 0.50 and net_change_pct >= 3.5:
                direction = "BULLISH" if window_closes[-1] > window_closes[0] else "BEARISH"
                trend_episodes.append({
                    "symbol": sym,
                    "start": buckets[i],
                    "end": buckets[i + window_size],
                    "direction": direction,
                    "move_pct": round(net_change_pct, 2),
                    "efficiency_ratio": round(er, 2),
                })
                
    print(f"Sustained Trend Episodes (4h+ window, ER>=0.50, Move>=3.5%): {len(trend_episodes)}")
    if trend_episodes:
        for ep in trend_episodes[:5]:
            print(f"  • [{ep['symbol']}] {ep['direction']} {ep['move_pct']}% (ER {ep['efficiency_ratio']}) from {ep['start'].strftime('%m-%d %H:%M')} to {ep['end'].strftime('%m-%d %H:%M')}")
    else:
        print("  • Nessun episodio di trend direzionale sostenuto rilevato finora (regime prevalente: micro-chop / lateralizzazione).")
        
    print("--------------------------------------------------")
    
    # 3. Readiness Evaluation
    is_ready = (days_span >= 14.0) and (len(trend_episodes) >= 2)
    print("STATO DI PRONTEZZA PER RIESEGUIRE IL BACKTEST:")
    if is_ready:
        print("🟢 PRONTO: Accumulati >14 giorni di dati e almeno 2 episodi di trend direzionale sostenuto.")
        print("   Azione consigliata: Rieseguire il backtest walk-forward esteso.")
    else:
        print("🔴 IN ATTESA: Accumulati {:.2f}/14.00 giorni e {}/2 episodi di trend direzionale.".format(days_span, len(trend_episodes)))
        print("   Azione consigliata: Mantenere le strategie disabilitate e continuare l'ingestione passiva.")
        
    print("==================================================")
    await conn.close()
    return is_ready

if __name__ == "__main__":
    asyncio.run(analyze_market_regime())
