import os
import time
import hmac
import hashlib
import httpx
from urllib.parse import urlencode

class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.base_url = "https://api.binance.com"

    def _generate_signature(self, query_string: str) -> str:
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    async def get_real_portfolio(self):
        if not self.api_key or not self.api_secret:
            return {"error": "Binance API keys not configured"}

        endpoint = "/api/v3/account"
        params = {
            "timestamp": int(time.time() * 1000)
        }
        query_string = urlencode(params)
        signature = self._generate_signature(query_string)
        
        headers = {
            "X-MBX-APIKEY": self.api_key
        }

        url = f"{self.base_url}{endpoint}?{query_string}&signature={signature}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                # Filter out zero balances
                balances = [b for b in data.get("balances", []) if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0]
                return balances
            else:
                return {"error": f"Failed to fetch portfolio: {response.text}"}
