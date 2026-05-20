#!/bin/sh
# Polls every 120 seconds for closed trades, logs to stdout
while true; do
    COUNT=$(psql -U crypto_user -d cryptoscalper_db -t -c "SELECT COUNT(*) FROM trades;")
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Closed trades in DB: $COUNT"
    if [ "$COUNT" -ge 5 ] 2>/dev/null; then
        echo "=== THRESHOLD REACHED: $COUNT trades closed ==="
        psql -U crypto_user -d cryptoscalper_db -c "
            SELECT 
                symbol, ab_variant, side,
                entry_price, exit_price,
                ROUND(pnl_usdt::numeric, 4) as pnl_usdt,
                ROUND(pnl_pct::numeric, 4) as pnl_pct,
                close_reason,
                ROUND(EXTRACT(EPOCH FROM (close_time - time))/60.0, 2) as holding_min
            FROM trades
            ORDER BY close_time DESC
            LIMIT 30;
        "
    fi
    sleep 120
done
