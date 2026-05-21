"""Load risk parameters from Redis override, shared config.yaml, then environment."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import yaml

SHARED_CONFIG_PATH = os.getenv("SHARED_CONFIG_PATH", "/app/shared_config/config.yaml")


@dataclass
class RiskParams:
    max_open_positions: int = 3
    max_exposure_per_symbol_usdt: Decimal = Decimal("1000.0")
    max_daily_loss_usdt: Decimal = Decimal("50.0")
    max_consecutive_losses: int = 5
    consecutive_loss_pause_minutes: int = 15
    risk_per_trade_pct: Decimal = Decimal("0.02")
    stop_loss_atr_multiplier: Decimal = Decimal("1.5")
    take_profit_atr_multiplier: Decimal = Decimal("3.0")
    commission_rate: Decimal = Decimal("0.001")
    min_profit_multiplier_vs_fees: Decimal = Decimal("3.0")
    entry_pullback_bps: Decimal = Decimal("18.0")
    min_atr_bps: Decimal = Decimal("8.0")
    pending_order_timeout_seconds: int = 300


def _from_env() -> RiskParams:
    return RiskParams(
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
        max_exposure_per_symbol_usdt=Decimal(os.getenv("MAX_EXPOSURE_PER_SYMBOL_USDT", "1000.0")),
        max_daily_loss_usdt=Decimal(os.getenv("MAX_DAILY_LOSS_USDT", "50.0")),
        max_consecutive_losses=int(os.getenv("MAX_CONSECUTIVE_LOSSES", "5")),
        consecutive_loss_pause_minutes=int(os.getenv("CONSECUTIVE_LOSS_PAUSE_MINUTES", "15")),
        risk_per_trade_pct=Decimal(os.getenv("RISK_PER_TRADE_PCT", "0.02")),
        stop_loss_atr_multiplier=Decimal(os.getenv("STOP_LOSS_ATR_MULTIPLIER", "1.5")),
        take_profit_atr_multiplier=Decimal(os.getenv("TAKE_PROFIT_ATR_MULTIPLIER", "3.0")),
        commission_rate=Decimal(os.getenv("COMMISSION_RATE", "0.001")),
        min_profit_multiplier_vs_fees=Decimal(os.getenv("MIN_PROFIT_MULTIPLIER_VS_FEES", "3.0")),
        entry_pullback_bps=Decimal(os.getenv("ENTRY_PULLBACK_BPS", "18.0")),
        pending_order_timeout_seconds=int(os.getenv("PENDING_ORDER_TIMEOUT_SECONDS", "300")),
    )


def _apply_yaml_risk(params: RiskParams, risk: dict[str, Any]) -> RiskParams:
    mapping = {
        "max_open_positions": ("max_open_positions", int),
        "max_exposure_per_symbol_usdt": ("max_exposure_per_symbol_usdt", Decimal),
        "max_daily_loss_usdt": ("max_daily_loss_usdt", Decimal),
        "max_consecutive_losses": ("max_consecutive_losses", int),
        "consecutive_loss_pause_minutes": ("consecutive_loss_pause_minutes", int),
        "risk_per_trade_pct": ("risk_per_trade_pct", Decimal),
        "stop_loss_atr_multiplier": ("stop_loss_atr_multiplier", Decimal),
        "take_profit_atr_multiplier": ("take_profit_atr_multiplier", Decimal),
        "commission_rate": ("commission_rate", Decimal),
        "min_profit_multiplier_vs_fees": ("min_profit_multiplier_vs_fees", Decimal),
        "entry_pullback_bps": ("entry_pullback_bps", Decimal),
        "min_atr_bps": ("min_atr_bps", Decimal),
        "pending_order_timeout_seconds": ("pending_order_timeout_seconds", int),
    }
    for yaml_key, (attr, cast) in mapping.items():
        if yaml_key in risk:
            setattr(params, attr, cast(str(risk[yaml_key])))
    return params


def _apply_redis_risk(params: RiskParams, cfg: dict[str, Any]) -> RiskParams:
    redis_mapping = {
        "MAX_OPEN_POSITIONS": ("max_open_positions", int),
        "MAX_EXPOSURE_PER_SYMBOL_USDT": ("max_exposure_per_symbol_usdt", Decimal),
        "MAX_DAILY_LOSS_USDT": ("max_daily_loss_usdt", Decimal),
        "MAX_CONSECUTIVE_LOSSES": ("max_consecutive_losses", int),
        "CONSECUTIVE_LOSS_PAUSE_MINUTES": ("consecutive_loss_pause_minutes", int),
        "RISK_PER_TRADE_PCT": ("risk_per_trade_pct", Decimal),
        "STOP_LOSS_ATR_MULTIPLIER": ("stop_loss_atr_multiplier", Decimal),
        "TAKE_PROFIT_ATR_MULTIPLIER": ("take_profit_atr_multiplier", Decimal),
        "COMMISSION_RATE": ("commission_rate", Decimal),
        "MIN_PROFIT_MULTIPLIER_VS_FEES": ("min_profit_multiplier_vs_fees", Decimal),
        "ENTRY_PULLBACK_BPS": ("entry_pullback_bps", Decimal),
        "MIN_ATR_BPS": ("min_atr_bps", Decimal),
        "PENDING_ORDER_TIMEOUT_SECONDS": ("pending_order_timeout_seconds", int),
    }
    for redis_key, (attr, cast) in redis_mapping.items():
        if redis_key in cfg:
            setattr(params, attr, cast(str(cfg[redis_key])))
    return params


async def load_risk_params(redis_client) -> RiskParams:
    """Priority: Redis config:risk > shared_config.yaml risk > environment."""
    params = _from_env()

    if os.path.exists(SHARED_CONFIG_PATH):
        try:
            with open(SHARED_CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f) or {}
            if "risk" in cfg and isinstance(cfg["risk"], dict):
                params = _apply_yaml_risk(params, cfg["risk"])
        except Exception:
            pass

    try:
        cfg_str = await redis_client.get("config:risk")
        if cfg_str:
            params = _apply_redis_risk(params, json.loads(cfg_str))
    except Exception:
        pass

    return params
