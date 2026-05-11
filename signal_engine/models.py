from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class NormalizedTick:
    symbol: str
    timestamp_ms: int
    type: str  # "trade", "bookTicker", "depth"
    price: Optional[float] = None
    qty: Optional[float] = None
    side: Optional[str] = None
    bid_price: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_price: Optional[float] = None
    ask_qty: Optional[float] = None
    bids: Optional[List[List[Any]]] = None
    asks: Optional[List[List[Any]]] = None

@dataclass
class MarketContext:
    price_history: Dict[str, List[float]]
    # Order book snapshots could be added here in the future

@dataclass
class Signal:
    symbol: str
    direction: str  # "BUY" or "SELL"
    strength: float # 0.0 to 1.0
    strategy_name: str
    timestamp_ms: int
    suggested_price: float
    suggested_qty: float
    is_shadow: bool = False # Used for A/B testing
