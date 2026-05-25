from reporter.phase1_report import GATE_MAX_DD_PCT, GATE_MIN_TRADES, GATE_PF_MIN, REQUIRED_CLEAN_DAYS, format_telegram


def test_phase1_telegram_report_includes_breakout1m_and_format() -> None:
    report = {
        "generated_at": "2026-05-24T08:00:00+00:00",
        "global": {
            "trades": 123,
            "profit_factor": 1.2345,
            "max_drawdown_pct": 4.56,
            "win_rate_pct": 51.2,
            "expectancy_bps": 3.21,
            "total_pnl_usdt": 12.345,
        },
        "by_strategy": {
            "EMA": {"trades": 10, "win_rate_pct": 50.0, "profit_factor": 1.234, "expectancy_bps": 1.23},
            "Momentum": {"trades": 20, "win_rate_pct": 55.0, "profit_factor": 1.1, "expectancy_bps": 2.0},
            "VWAP": {"trades": 30, "win_rate_pct": 40.0, "profit_factor": 0.9, "expectancy_bps": -0.5},
        },
        "breakout_1m": {"trades": 7, "win_rate_pct": 42.0, "profit_factor": 0.98, "expectancy_bps": -0.4},
        "infrastructure": {
            "bot_status": "running",
            "circuit_open": False,
            "safe_mode": False,
            "ingestion_stale": False,
            "db_ticks_count": 1000,
            "positions_db_count": 2,
            "risk_received": 20,
            "risk_approved": 10,
            "risk_approved_pct": 50.0,
            "risk_rejected": 10,
            "risk_rejected_low_profit": 3,
            "risk_rejected_low_volatility": 5,
            "risk_rejected_other": 2,
            "atr_btc": "12.3 bps",
            "atr_eth": "34.5 bps",
            "atr_fallback_active": False,
            "atr_last_fallback_min": 0.0,
            "pending_placed": 5,
            "pending_filled": 4,
            "pending_fill_rate_pct": 80.0,
            "pending_cancelled": 1,
            "pending_escaped": 0,
        },
        "gate": {"consecutive_clean_days": 2, "gate_exit_ready": False, "last_reset_reason": "config change"},
    }

    text = format_telegram(report, "3/14")
    expected = "\n".join(
        [
            "📅 FASE 1 — Giorno 3/14 — 2026-05-24",
            "",
            "GLOBAL",
            f"Trades: 123 | PF: 1.23 (gate >{GATE_PF_MIN}, n>={GATE_MIN_TRADES})",
            f"Max DD: 4.6% (gate <{GATE_MAX_DD_PCT:.1f}%)",
            "Win rate: 51% | E: 3.21 bps",
            "PnL: $12.35 | Equity paper: $0.00",
            "",
            "STRATEGIE",
            "EMA:        n=10  WR=50%  PF=1.23  E=1.2bps",
            "Momentum:   n=20  WR=55%  PF=1.10  E=2.0bps",
            "VWAP:       n=30  WR=40%  PF=0.90  E=-0.5bps",
            "Breakout1m: n=7  WR=42%  PF=0.98  E=-0.4bps",
            "",
            "INFRA",
            "Bot: running | CB: closed",
            "Safe mode: off | Ingestion stale: no",
            "Ticks DB: 1000 | Pos aperte: 2",
            "",
            "⚡ RISK MANAGER (ultima ora)",
            "Segnali ricevuti: 20",
            "Approvati: 10 (50%)",
            "Rifiutati: 10",
            "  → Low profitability: 3",
            "  → Low volatility: 5",
            "  → Altri: 2",
            "ATR live: BTC=12.3 bps | ETH=34.5 bps",
            "ATR fallback: ❌ Mai",
            "",
            "📋 ORDINI PENDENTI (ultime 24h)",
            "Piazzati: 5",
            "Fillati: 4 (80% fill rate)",
            "Cancellati (timeout): 1",
            "Scappati senza fill: 0",
            "",
            "🎯 GATE",
            f"Giorni puliti consecutivi: 2/{REQUIRED_CLEAN_DAYS}",
            "Exit ready: NO",
            "Ultimo reset: config change",
        ]
    )

    assert text == expected

