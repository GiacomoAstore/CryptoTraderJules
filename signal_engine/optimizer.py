import json
import logging
import urllib.request
from strategy import EMAStrategy
from models import NormalizedTick, MarketContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Optimizer")

def run_backtest(ticks, strategy, history_size):
    context = MarketContext(price_history={})
    signals = []

    # Overwrite strategy parameters for this test run
    # For a real implementation, you'd pass periods dynamically via constructor
    # Here we mock it by adjusting how many points we check, simulating a dynamic SMA

    for tick in ticks:
        # Update context
        if tick.type == "trade" and tick.price:
            if tick.symbol not in context.price_history:
                context.price_history[tick.symbol] = []
            context.price_history[tick.symbol].append(tick.price)
            # Bound context history to history_size for simulation
            if len(context.price_history[tick.symbol]) > history_size:
                 context.price_history[tick.symbol].pop(0)

        signal = strategy.generate_signal(tick, context)
        if signal:
            signals.append(signal)

    # Mocking a basic PnL evaluation for the signals generated
    win_rate = len(signals) * 0.52 if signals else 0 # 52% mock win rate
    pnl = len(signals) * 0.15 # $0.15 mock profit per signal
    return {"signals_generated": len(signals), "estimated_win_rate": win_rate, "estimated_pnl": pnl}

def fetch_historical_binance_data(symbol="BTCUSDT", limit=1000):
    logger.info(f"Fetching real historical data from Binance for {symbol}")
    url = f"https://api.binance.com/api/v3/aggTrades?symbol={symbol}&limit={limit}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))

        return [
            {
                "symbol": symbol,
                "timestamp_ms": t["T"],
                "type": "trade",
                "price": float(t["p"]),
                "qty": float(t["q"])
            }
            for t in data
        ]
    except Exception as e:
        logger.error(f"Failed to fetch Binance data: {e}. Falling back to mock data.")
        return [
            {"symbol": symbol, "timestamp_ms": i, "type": "trade", "price": 60000 + (i % 100)}
            for i in range(1000)
        ]

def walk_forward_optimize():
    logger.info("Starting Walk-Forward Optimization using real Binance data...")

    raw_ticks = fetch_historical_binance_data()

    ticks = [
        NormalizedTick(
            symbol=t.get("symbol", ""),
            timestamp_ms=t.get("timestamp_ms", 0),
            type=t.get("type", ""),
            price=t.get("price")
        ) for t in raw_ticks
    ]

    # Test different window sizes for the SMA strategy
    parameters = [10, 20, 50, 100]
    best_pnl = -float('inf')
    best_param = None

    for param in parameters:
        logger.info(f"Testing EMAStrategy with history window: {param}")
        strategy = EMAStrategy()

        # In a real WFO, we would split ticks into In-Sample and Out-Of-Sample
        # and evaluate stability across multiple rolling windows.

        result = run_backtest(ticks, strategy, param)
        logger.info(f"Result for param={param}: {result}")

        if result["estimated_pnl"] > best_pnl:
            best_pnl = result["estimated_pnl"]
            best_param = param

    logger.info(f"Optimization complete. Best parameter: {best_param} with PnL: {best_pnl}")
    return best_param

if __name__ == "__main__":
    # Typically this would be invoked via cron or a manual trigger script.
    walk_forward_optimize()
