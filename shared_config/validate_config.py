"""Pydantic schema for shared_config/config.yaml (signal engine + LLM optimizer)."""
from __future__ import annotations

from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALLOWED_STRATEGIES = frozenset({
    "EMAStrategy",
    "EMACrossoverStrategy",
    "EmaCrossoverStrategy",
    "OrderBookImbalanceStrategy",
    "VWAPDeviationStrategy",
    "MomentumBurstStrategy",
    "VolatilityExpansionStrategy",
    "BollingerMeanReversionStrategy",
    "MicroStructureBreakoutStrategy",
})


class SignalFiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_spread_bps: float = Field(default=10, gt=0, le=100)
    min_atr_pct: float = Field(default=0.0006, gt=0)
    max_atr_pct: float = Field(default=0.025, gt=0)
    min_volume_window: float = Field(default=0, ge=0)
    volume_window_ms: int = Field(default=3000, ge=500)
    max_chop_ratio: float = Field(default=6, gt=0)
    commission_rate: float = Field(default=0.001, ge=0, le=0.1)
    min_edge_vs_fees_mult: float = Field(default=2.5, ge=1)
    min_expected_move_bps: float = Field(default=12, gt=0)


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    enabled: bool = True
    weight: float = Field(default=1.0, gt=0, le=10)
    variant_a: Optional[dict[str, Any]] = None
    variant_b: Optional[dict[str, Any]] = None
    params: Optional[dict[str, Any]] = None

    @field_validator("name")
    @classmethod
    def validate_strategy_name(cls, value: str) -> str:
        if value not in ALLOWED_STRATEGIES:
            raise ValueError(f"Unknown strategy '{value}'. Allowed: {sorted(ALLOWED_STRATEGIES)}")
        return value

    @model_validator(mode="after")
    def require_variant_params(self) -> "StrategyConfig":
        if not any([self.variant_a, self.variant_b, self.params]):
            raise ValueError(f"Strategy '{self.name}' must define variant_a, variant_b, or params")
        return self


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_open_positions: int = Field(default=3, ge=1, le=50)
    max_exposure_per_symbol_usdt: float = Field(default=1000.0, gt=0)
    max_daily_loss_usdt: float = Field(default=50.0, gt=0)
    max_consecutive_losses: int = Field(default=5, ge=1, le=50)
    consecutive_loss_pause_minutes: int = Field(default=15, ge=1, le=10080)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    stop_loss_atr_multiplier: float = Field(default=1.5, gt=0, le=20)
    take_profit_atr_multiplier: float = Field(default=3.0, gt=0, le=50)
    commission_rate: float = Field(default=0.001, ge=0, le=0.1)
    min_profit_multiplier_vs_fees: float = Field(default=3.0, ge=1, le=20)
    entry_pullback_bps: float = Field(default=18.0, ge=0, le=200.0)
    pending_order_timeout_seconds: int = Field(default=300, ge=5, le=86400)


class ConsensusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: int = Field(default=2, ge=1, le=20)


class SignalEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregation: str = Field(default="momentum_ema_strict")
    ema_min_separation_bps: float = Field(default=3, ge=0, le=100)
    max_signals_per_hour_per_symbol: int = Field(default=15, ge=0, le=1000)

    @field_validator("aggregation")
    @classmethod
    def validate_aggregation(cls, value: str) -> str:
        if value != "momentum_ema_strict":
            raise ValueError(
                f"Unsupported signal_engine.aggregation '{value}'. "
                "Use momentum_ema_strict."
            )
        return value


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategies: list[StrategyConfig] = Field(min_length=1)
    risk: Optional[RiskConfig] = None
    consensus: Optional[ConsensusConfig] = None
    min_consensus: Optional[int] = Field(default=None, ge=1, le=20)
    signal_filters: Optional[SignalFiltersConfig] = None
    signal_engine: Optional[SignalEngineConfig] = None


def validate_config_dict(data: dict[str, Any]) -> AppConfig:
    return AppConfig.model_validate(data)


def validate_config_yaml(yaml_text: str) -> AppConfig:
    parsed = yaml.safe_load(yaml_text)
    if not isinstance(parsed, dict):
        raise ValueError("Config root must be a mapping")
    return validate_config_dict(parsed)


def validate_config_file(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        return validate_config_yaml(f.read())
