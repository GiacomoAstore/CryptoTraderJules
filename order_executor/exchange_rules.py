"""LOT_SIZE / MIN_NOTIONAL filters from Crypto.com get-instruments."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

D = Decimal


def binance_to_cryptocom_symbol(symbol: str) -> str:
    """Convert Binance-style 'BTCUSDT' to Crypto.com-style 'BTC_USDT'.

    Handles common quote currencies: USDT, USDC, USD, BTC, ETH, CRO.
    """
    symbol = symbol.upper()
    if "_" in symbol:
        return symbol

    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "CRO"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}_{quote}"
    return f"{symbol[:-4]}_{symbol[-4:]}" if len(symbol) > 4 else symbol


class SymbolFilters:
    def __init__(self, symbol: str, instrument_data: dict[str, Any]):
        self.symbol = symbol.upper()

        qty_decimals = int(instrument_data.get("quantity_decimals", 5))
        price_decimals = int(instrument_data.get("price_decimals", 2))

        # Use actual tick sizes if present, fallback to decimals power of 10
        self.step_size = D(str(instrument_data.get("qty_tick_size", 10 ** -qty_decimals)))
        self.tick_size = D(str(instrument_data.get("price_tick_size", 10 ** -price_decimals)))

        self.min_qty = self.step_size
        self.max_qty = D("999999999.0")
        self.min_notional = D("0.0")

    def round_qty(self, qty: D) -> D:
        if self.step_size <= 0:
            return qty
        steps = (qty / self.step_size).to_integral_value(rounding=ROUND_DOWN)
        rounded = steps * self.step_size
        return max(rounded, D("0"))

    def round_price(self, price: D) -> D:
        if self.tick_size <= 0:
            return price
        steps = (price / self.tick_size).to_integral_value(rounding=ROUND_DOWN)
        return steps * self.tick_size

    def validate_order(self, side: str, qty: D, price: D) -> tuple[bool, str]:
        q = self.round_qty(qty)
        if q < self.min_qty:
            return False, f"qty {q} < minQty {self.min_qty}"
        if q > self.max_qty:
            return False, f"qty {q} > maxQty {self.max_qty}"
        notional = q * price
        if self.min_notional > 0 and notional < self.min_notional:
            return False, f"notional {notional} < minNotional {self.min_notional}"
        return True, "ok"


def parse_symbol_filters(instruments_response: dict, symbol: str) -> SymbolFilters:
    """Parse Crypto.com get-instruments response into SymbolFilters.

    Crypto.com response structure:
      {"data": [{"symbol": "BTC_USDT", "quantity_decimals": 4, ...}, ...]}
    """
    data_list = instruments_response.get("data", [])
    target = binance_to_cryptocom_symbol(symbol).upper()
    for inst in data_list:
        if inst.get("symbol", "").upper() == target:
            return SymbolFilters(symbol, inst)
    raise KeyError(f"Symbol {symbol} (as {target}) not in get-instruments response")


