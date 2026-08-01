"""
Abstract Exchange Adapter — facilitates future exchange swaps.

All exchange-specific clients must implement this interface.
The rest of the app depends ONLY on this abstraction.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any


class ExchangeAdapter(ABC):
    """Abstract interface for exchange integration."""

    @abstractmethod
    async def get_instruments(self, instrument: str | None = None) -> dict:
        """Return instrument metadata (filters, precision, etc.)."""
        ...

    @abstractmethod
    async def get_account_summary(self) -> dict:
        """Return account balances."""
        ...

    @abstractmethod
    async def create_order(
        self,
        instrument: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None = None,
        time_in_force: str | None = None,
    ) -> dict:
        """Place an order. Returns exchange order response."""
        ...

    @abstractmethod
    async def cancel_order(self, instrument: str, order_id: str) -> dict:
        """Cancel a single order by ID."""
        ...

    @abstractmethod
    async def cancel_all_orders(self, instrument: str) -> list:
        """Cancel all open orders for an instrument."""
        ...

    @abstractmethod
    async def get_open_orders(self, instrument: str | None = None) -> list:
        """List open orders, optionally filtered by instrument."""
        ...

    @abstractmethod
    async def get_ticker(self, instrument: str) -> dict:
        """Get current ticker (best bid/ask, last price)."""
        ...

    @abstractmethod
    async def get_book(self, instrument: str, depth: int = 20) -> dict:
        """Get order book snapshot."""
        ...

    @abstractmethod
    async def get_candlestick(
        self, instrument: str, timeframe: str = "1m", count: int = 300
    ) -> list[dict]:
        """Get OHLCV candlestick data."""
        ...
