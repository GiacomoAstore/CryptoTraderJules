"""LOT_SIZE / MIN_NOTIONAL filters from exchangeInfo (Phase 2 scaffold)."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

D = Decimal


class SymbolFilters:
    def __init__(self, symbol: str, filters: list[dict[str, Any]]):
        self.symbol = symbol.upper()
        self.min_qty = D("0")
        self.max_qty = D("999999")
        self.step_size = D("0.00001")
        self.min_notional = D("0")
        self.tick_size = D("0.01")

        for f in filters:
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                self.min_qty = D(str(f["minQty"]))
                self.max_qty = D(str(f["maxQty"]))
                self.step_size = D(str(f["stepSize"]))
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                self.min_notional = D(str(f.get("minNotional") or f.get("notional", "0")))
            elif ft == "PRICE_FILTER":
                self.tick_size = D(str(f["tickSize"]))

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


def parse_symbol_filters(exchange_info: dict, symbol: str) -> SymbolFilters:
    for s in exchange_info.get("symbols", []):
        if s.get("symbol") == symbol.upper():
            return SymbolFilters(symbol, s.get("filters", []))
    raise KeyError(f"Symbol {symbol} not in exchangeInfo")
