#!/usr/bin/env python3
"""
Offline strategy evaluation with realistic friction (spread, fees, slippage).
Uses Binance public klines when network available; falls back to synthetic data.

  python scripts/strategy_eval.py
  python scripts/strategy_eval.py --symbol BTCUSDT --interval 1m --bars 2000
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Deque, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "signal_engine"))

import yaml
import strategy
from indicators import atr_from_ticks, spread_bps
from market_filters import FilterParams, build_snapshot, passes_market_filters

D = Decimal


@dataclass
class SimTrade:
    side: str
    entry: D
    exit: D
    pnl_bps: D
    reason: str


def load_config():
    path = ROOT / "shared_config" / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_strategies(cfg: dict) -> list:
    strats = []
    for s in cfg.get("strategies", []):
        if not s.get("enabled", True):
            continue
        name = s["name"]
        if name in ("EMAStrategy", "EmaCrossoverStrategy"):
            name = "EMACrossoverStrategy"
        weight = D(str(s.get("weight", 1)))
        for key, variant in [("variant_a", "A"), ("variant_b", "B")]:
            if key in s:
                params = {**s[key], "weight": weight, "ab_variant": variant}
                cls = getattr(strategy, name, None)
                if cls:
                    strats.append(cls(params))
    return strats


def fetch_klines(symbol: str, interval: str, limit: int) -> list[dict]:
    try:
        import httpx
    except ImportError:
        return []

    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    with httpx.Client(timeout=20) as client:
        r = client.get(url, params=params)
        if r.status_code != 200:
            return []
        bars = []
        for row in r.json():
            bars.append({
                "open": D(row[1]),
                "high": D(row[2]),
                "low": D(row[3]),
                "close": D(row[4]),
                "volume": D(row[5]),
                "ts": int(row[0]),
            })
        return bars


def synthetic_bars(n: int, start: D = D("65000")) -> list[dict]:
    random.seed(42)
    price = start
    bars = []
    for i in range(n):
        shock = D(str(random.gauss(0, 0.0008)))
        o = price
        c = price * (D("1") + shock)
        h = max(o, c) * (D("1") + D("0.0003"))
        l = min(o, c) * (D("1") - D("0.0003"))
        vol = D(str(abs(random.gauss(2, 1))))
        bars.append({"open": o, "high": h, "low": l, "close": c, "volume": vol, "ts": i * 60000})
        price = c
    return bars


def bar_to_ticks(bar: dict, symbol: str, n_ticks: int = 12) -> list[dict]:
    """Expand 1m bar into synthetic ticks for microstructure strategies."""
    spread = bar["close"] * D("0.00005")
    bid = bar["close"] - spread / D("2")
    ask = bar["close"] + spread / D("2")
    ticks = []
    step_ms = 60000 // n_ticks
    qty_each = bar["volume"] / D(str(n_ticks * 2))
    for i in range(n_ticks):
        frac = D(str(i + 1)) / D(str(n_ticks + 1))
        px = bar["low"] + (bar["high"] - bar["low"]) * frac
        side = "BUY" if px >= bar["open"] else "SELL"
        bid_qty = D("14") if side == "BUY" else D("7")
        ask_qty = D("7") if side == "BUY" else D("14")
        ticks.append({
            "symbol": symbol,
            "price": px,
            "qty": max(qty_each, D("0.01")),
            "side": side,
            "timestamp_ms": bar["ts"] + i * step_ms,
            "bid_price": bid,
            "ask_price": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
        })
    return ticks


def simulate_strategy(
    strat,
    bars: list[dict],
    symbol: str,
    filter_params: FilterParams,
    fee_rate: D = D("0.001"),
    slippage_bps: D = D("2"),
) -> dict:
    price_hist: Deque = deque(maxlen=120)
    tick_hist: Deque = deque(maxlen=120)
    trades: list[SimTrade] = []
    position: Optional[dict] = None
    atr_sl_mult = D("1.8")
    atr_tp_mult = D("3.5")

    for bar in bars:
        for tick in bar_to_ticks(bar, symbol):
            price_hist.append(tick["price"])
            tick_hist.append(tick)

            snapshot = build_snapshot(tick, tick_hist)
            ctx = strategy.MarketContext(
                price_history=price_hist,
                tick_history=tick_hist,
                current_position=position,
                atr=snapshot.atr,
                atr_pct=snapshot.atr_pct,
                spread_bps=snapshot.spread_bps,
            )

            if position:
                atr = snapshot.atr if snapshot.atr > D("0") else tick["price"] * D("0.001")
                sl = position["sl"]
                tp = position["tp"]
                px = tick["price"]
                if position["side"] == "BUY":
                    if px <= sl:
                        trades.append(_close(position, sl, fee_rate, slippage_bps, "SL"))
                        position = None
                    elif px >= tp:
                        trades.append(_close(position, tp, fee_rate, slippage_bps, "TP"))
                        position = None
                else:
                    if px >= sl:
                        trades.append(_close(position, sl, fee_rate, slippage_bps, "SL"))
                        position = None
                    elif px <= tp:
                        trades.append(_close(position, tp, fee_rate, slippage_bps, "TP"))
                        position = None
                continue

            sig = strat.generate_signal(tick, ctx)
            if not sig or position:
                continue
            ok, _ = passes_market_filters(snapshot, filter_params, sig.expected_edge_bps)
            if not ok:
                continue

            atr = snapshot.atr if snapshot.atr > D("0") else tick["price"] * D("0.001")
            slip = tick["price"] * slippage_bps / D("10000")
            if sig.direction == "BUY":
                entry = tick["ask_price"] + slip
                sl = entry - atr * atr_sl_mult
                tp = entry + atr * atr_tp_mult
            else:
                entry = tick["bid_price"] - slip
                sl = entry + atr * atr_sl_mult
                tp = entry - atr * atr_tp_mult

            position = {"side": sig.direction, "entry": entry, "sl": sl, "tp": tp}

    if position:
        last = bars[-1]["close"]
        trades.append(_close(position, last, fee_rate, slippage_bps, "EOD"))

    return _metrics(trades, strat.name)


def _close(pos: dict, exit_px: D, fee: D, slip_bps: D, reason: str) -> SimTrade:
    slip = exit_px * slip_bps / D("10000")
    if pos["side"] == "BUY":
        exit_adj = exit_px - slip
        gross = (exit_adj - pos["entry"]) / pos["entry"]
    else:
        exit_adj = exit_px + slip
        gross = (pos["entry"] - exit_adj) / pos["entry"]
    net = gross - fee * D("2")
    return SimTrade(pos["side"], pos["entry"], exit_adj, net * D("10000"), reason)


def _metrics(trades: list[SimTrade], name: str) -> dict:
    if not trades:
        return {"name": name, "trades": 0, "expectancy_bps": 0, "win_rate": 0, "profit_factor": 0, "max_dd_bps": 0}

    wins = [t for t in trades if t.pnl_bps > 0]
    losses = [t for t in trades if t.pnl_bps <= 0]
    gross_win = sum(t.pnl_bps for t in wins) if wins else D("0")
    gross_loss = abs(sum(t.pnl_bps for t in losses)) if losses else D("1")
    pf = float(gross_win / gross_loss) if gross_loss > 0 else 999.0

    equity = D("0")
    peak = D("0")
    max_dd = D("0")
    for t in trades:
        equity += t.pnl_bps
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    rets = [float(t.pnl_bps) for t in trades]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = var ** 0.5
    sharpe = (mean / std) * (len(rets) ** 0.5) if std > 1e-9 else 0.0

    return {
        "name": name,
        "trades": len(trades),
        "expectancy_bps": float(mean),
        "win_rate": len(wins) / len(trades) * 100,
        "profit_factor": pf,
        "max_dd_bps": float(max_dd),
        "sharpe": sharpe,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--bars", type=int, default=1500)
    args = parser.parse_args()

    cfg = load_config()
    sf = cfg.get("signal_filters", {})
    filter_params = FilterParams(
        max_spread_bps=D(str(sf.get("max_spread_bps", 10))),
        min_atr_pct=D(str(sf.get("min_atr_pct", "0.0006"))),
        max_atr_pct=D(str(sf.get("max_atr_pct", "0.025"))),
        min_volume_window=D(str(sf.get("min_volume_window", 0))),
        volume_window_ms=int(sf.get("volume_window_ms", 3000)),
        max_chop_ratio=D(str(sf.get("max_chop_ratio", 5.5))),
        commission_rate=D(str(sf.get("commission_rate", "0.001"))),
        min_edge_vs_fees_mult=D(str(sf.get("min_edge_vs_fees_mult", "2.5"))),
        min_expected_move_bps=D(str(sf.get("min_expected_move_bps", 12))),
    )

    bars = fetch_klines(args.symbol, args.interval, args.bars)
    data_src = "binance"
    if not bars:
        bars = synthetic_bars(args.bars)
        data_src = "synthetic"

    print(f"=== Strategy eval ({data_src}) {args.symbol} {args.interval} n={len(bars)} ===\n")
    print(f"{'Strategy':<35} {'Trades':>6} {'E[bps]':>8} {'Win%':>7} {'PF':>6} {'Sharpe':>7} {'MaxDD':>8}")
    print("-" * 86)

    results = []
    for strat in build_strategies(cfg):
        m = simulate_strategy(strat, bars, args.symbol, filter_params)
        results.append(m)
        print(
            f"{m['name']:<35} {m['trades']:>6} {m['expectancy_bps']:>8.2f} "
            f"{m['win_rate']:>6.1f} {m['profit_factor']:>6.2f} {m.get('sharpe', 0):>7.2f} "
            f"{m['max_dd_bps']:>8.1f}"
        )

    viable = [r for r in results if r["trades"] >= 10 and r["expectancy_bps"] > 0 and r["profit_factor"] > 1.05]
    print(f"\nViable (E>0, PF>1.05, trades>=10): {len(viable)}/{len(results)}")
    sys.exit(0 if viable else 1)


if __name__ == "__main__":
    main()
