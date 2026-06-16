import asyncio
import time
import os
import aiohttp
import urllib.parse
import hmac
import hashlib

# Mock classes to avoid full dependencies
class MockResponse:
    def __init__(self, status):
        self.status = status
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    async def json(self):
        return {}

class MockSession:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    def delete(self, url, headers=None):
        # Simulate network latency
        return MockResponse(200)
    def post(self, url, headers=None):
        return MockResponse(200)

async def run_benchmark(num_positions):
    from live_executor.main import live_open_positions, format_precision, exchange_info_cache

    # Setup dummy data
    live_open_positions.clear()
    exchange_info_cache.clear()

    symbol_base = "BTCUSDT"
    for i in range(num_positions):
        sym = f"{symbol_base}_{i}"
        exchange_info_cache[sym] = {"stepSize": "0.001"}
        if sym not in live_open_positions:
            live_open_positions[sym] = []
        live_open_positions[sym].append({
            "oco_order_list_id": f"oco_{i}",
            "side": "BUY",
            "qty": 1.5,
        })

    secret_key = "dummy_secret"
    api_key = "dummy_api"
    base_url = "https://api.binance.com"
    headers = {"X-MBX-APIKEY": api_key}

    start_time = time.time()

    # Concurrent implementation
    async with aiohttp.ClientSession() as real_session:
        session = MockSession()
        async def cancel_and_liquidate(symbol, pos):
            # 1. Cancel existing OCO orders if possible
            if pos.get("oco_order_list_id"):
                cancel_params = {
                    "symbol": symbol,
                    "orderListId": pos["oco_order_list_id"],
                    "timestamp": int(time.time() * 1000)
                }
                qs = urllib.parse.urlencode(cancel_params)
                sig = hmac.new(secret_key.encode('utf-8'), qs.encode('utf-8'), hashlib.sha256).hexdigest()
                cancel_url = f"{base_url}/api/v3/orderList?{qs}&signature={sig}"
                async with session.delete(cancel_url, headers=headers) as resp:
                    await asyncio.sleep(0.01) # simulate small delay

            # 2. Liquidate with MARKET order
            liq_side = "SELL" if pos["side"] == "BUY" else "BUY"

            sym_info = exchange_info_cache.get(symbol, {})
            step_size = sym_info.get("stepSize", "0.001")
            liq_qty = format_precision(pos["qty"], step_size)

            liq_params = {
                "symbol": symbol,
                "side": liq_side,
                "type": "MARKET",
                "quantity": liq_qty,
                "timestamp": int(time.time() * 1000)
            }
            qs2 = urllib.parse.urlencode(liq_params)
            sig2 = hmac.new(secret_key.encode('utf-8'), qs2.encode('utf-8'), hashlib.sha256).hexdigest()
            liq_url = f"{base_url}/api/v3/order?{qs2}&signature={sig2}"

            async with session.post(liq_url, headers=headers) as resp:
                await asyncio.sleep(0.01) # simulate small delay

        tasks = []
        for symbol, positions in list(live_open_positions.items()):
            for pos in positions:
                tasks.append(cancel_and_liquidate(symbol, pos))
        if tasks:
            await asyncio.gather(*tasks)

    end_time = time.time()
    print(f"Concurrent took: {end_time - start_time:.4f}s for {num_positions} positions")

if __name__ == "__main__":
    asyncio.run(run_benchmark(50))
