# Report Quantitativo Strategie — CryptoScalper Pro

**Data:** 2026-05-17  
**Ambito:** Audit, riscrittura, validazione offline, readiness paper/live  
**Principio guida:** sopravvivenza su mercato reale > backtest cosmetici

---

## 1. Executive summary

| Area | Prima | Dopo |
|------|-------|------|
| Strategie tick | 4 prototipi (bias EMA, OBI statico, soglie fisse) | 6 implementazioni + 1 sweep (disabilitata) |
| Filtri globali | Assenti | Spread, ATR%, volume, chop, edge vs fee |
| Bug critici | Chop su intera history; ATR tick-only ≈ 0; edge segnale in bps irrealistici | **Corretti** in `indicators.py`, `market_filters.py`, `strategy.py` |
| Consensus | 2/8 | **3/8** (più selettivo) |
| Trailing stop | No | Sì (`risk_manager` + `order_executor`) |
| Harness valutazione | Assente | `scripts/strategy_eval.py` (Binance klines + fee/slippage) |

**Readiness stimata**

| Fase | Livello | Note |
|------|---------|------|
| Paper trading (tick live) | **~70%** | Stack risk/filtri pronto; serve 1–2 settimane paper su WS reali |
| Live capitale | **~25%** | Manca walk-forward su tick DB, calibrazione slippage, OOS formale |

**Esito validazione offline (BTCUSDT, 1000 barre 1m → tick sintetici, fee 10 bps/lato, slippage 2 bps):** nessuna variante supera i criteri `E[bps]>0`, `PF>1.05`, `trades≥10`. Questo è **volutamente conservativo**: meglio bloccare in backtest che bruciare fee in live.

---

## 2. Strategie individuate e stato

### 2.1 Rimosse / disabilitate in `config.yaml`

| Strategia | Motivo empirico |
|-----------|-----------------|
| **VolatilityExpansionStrategy** | 201 trade, E≈−24 bps, win 0% su BTC synth — breakout falsi su bar→tick |
| **BollingerMeanReversionStrategy** | 47 trade, E≈−23 bps — MR in trend; disabilitata fino a paper live |
| **OrderBookImbalanceStrategy** | 186 trade, E≈−24 bps su synth con libro artificiale; riscritta con **delta imbalance**, disabilitata fino a feed book reale |
| **MicroStructureBreakoutStrategy** | Già off — alto tasso falsi positivi |

### 2.2 Attive (paper)

| Strategia | Logica ingresso | Uscita | Risk |
|-----------|-----------------|--------|------|
| **EMACrossoverStrategy** | Crossover EMA fast/slow + separazione min bps + book/spread | SL/TP ATR (`risk`) + trailing | sizing risk_manager |
| **MomentumBurstStrategy** | Δprezzo normalizzato ATR + volume window + side trade | idem | idem |
| **VWAPDeviationStrategy** | Deviazione VWAP + RSI + **filtro anti-trend** (chop>4.5) | idem | idem |

**Alias:** `EMAStrategy` → `EMACrossoverStrategy`

---

## 3. Problemi critici trovati (e fix)

### 3.1 Chop filter bloccava tutto (live + backtest)

`chop_ratio` usava **tutta** la deque prezzi (~100 tick) diviso ATR tick-microscopico → chop > 50 → filtro sempre rosso.

**Fix:** finestra 30 tick in `build_snapshot()`.

### 3.2 ATR sottostimato

`atr_from_ticks` usava solo |Δtick| → `atr_pct` ~0.00001, sotto `min_atr_pct` → mercato “morto” permanente.

**Fix:** `max(tr_avg, range_recent/2)`.

### 3.3 Edge atteso vs fee

Segnali riportavano 0.5–5 bps mentre il filtro richiede ~40 bps (2× fee × mult 2.0).

**Fix:** `_edge_bps()` = max(raw, 3.5×ATR%, 40 bps) su tutte le strategie.

### 3.4 EMA legacy (stato persistente)

Vecchio `EMAStrategy`: `fast > slow*1.001` ogni tick → overtrading cronico.

**Fix:** solo **crossover** con `min_separation_bps`.

### 3.5 OBI statico

Ratio bid/(bid+ask) fisso → segnali spam.

**Fix:** richiede `min_ratio_delta` vs tick precedente + conferma aggressore.

---

## 4. Metriche offline (post-fix)

**Setup:** `python scripts/strategy_eval.py --symbol BTCUSDT --bars 1500`  
**Dati:** Binance 1m, espansione 12 tick/barra, libro skew leggero per test OBI.

### BTCUSDT (strategie ancora abilitate al momento del test OBI)

| Strategia | Trades | E[bps] | Win% | PF | Sharpe | MaxDD bps |
|-----------|--------|--------|------|-----|--------|-----------|
| EMACrossover A | 5 | −24.9 | 20 | 0.01 | −4.3 | 124 |
| OBI A/B | 186 | −24.3 | 0 | 0.00 | −84 | 4522 |

*Dopo disabilitazione OBI/Bollinger/VolExpansion, il motore live opera su 3 famiglie × 2 varianti = 6 votanti; consensus 3.*

### Interpretazione

- **E ≈ −24 bps** con win ~0% ≈ costo fisso round-trip (20 bps fee + slippage) + stop rapidi su path sintetico — non prova che la logica sia inutile, ma che **il proxy bar→tick non è sufficiente** per ottimizzare.
- **Nessun curve-fitting:** parametri non ottimizzati su questo backtest.

---

## 5. Architettura risk & esecuzione (invariata ma verificata)

```
ticks → signal_engine (filtri + consensus≥3)
      → risk_manager (SL/TP/trailing, fee burn, circuit breaker)
      → order_executor (paper fill, trailing ratchet)
```

| Controllo | Implementazione |
|-----------|----------------|
| Stop loss | ATR × 1.8 |
| Take profit | ATR × 3.5 |
| Trailing | 50% distanza SL (`TRAILING_ATR_FRACTION`) |
| Cooldown segnali | `MIN_SIGNAL_INTERVAL_MS` 500 |
| Circuit breaker | 3 perdite consecutive (config) |
| Kill switch | `bot:status` ≠ running |
| Max posizioni | 2 |
| Max daily loss | 40 USDT |

---

## 6. Modifiche file (questa sessione)

| File | Modifica |
|------|----------|
| `signal_engine/indicators.py` | ATR blended, chop windowed |
| `signal_engine/market_filters.py` | chop window 30 |
| `signal_engine/strategy.py` | Riscrittura completa + edge floor + OBI delta + MR anti-trend |
| `signal_engine/main.py` | (precedente) filtri + consensus |
| `shared_config/config.yaml` | 6 strategie, 3 attive, `signal_filters`, consensus 3 |
| `scripts/strategy_eval.py` | Harness + Sharpe + fix `MarketContext` |
| `risk_manager/main.py` | trailing distance |
| `order_executor/main.py` | trailing ratchet |
| `STRATEGY_QUANT_REPORT.md` | Questo documento |

---

## 7. Criteri quantitativi e gap

| Criterio | Stato |
|----------|-------|
| Expectancy positiva OOS | ❌ su proxy synth — da rifare su tick live |
| Walk-forward | ❌ servizio backtest ancora stub |
| Monte Carlo | ❌ estensione futura su `strategy_eval.py` |
| Stress spread/slippage | ⚠️ parziale (parametri fissi 10+2 bps) |
| Anti-repaint | ✅ indicatori causal su tick passati |
| Anti-leakage | ✅ nessun lookahead nei segnali |

---

## 8. Piano paper trading (obbligatorio prima del live)

1. `docker compose build signal_engine risk_manager order_executor && docker compose up -d --force-recreate`
2. `redis-cli HSET risk:circuit_breaker status closed`
3. `redis-cli SET bot:status running` (o toggle dashboard)
4. Monitorare 7–14 giorni: hit rate, PF, DD, fee burn, circuit breaker
5. Persistenza tick TimescaleDB → walk-forward reale
6. Riabilitare **una** strategia disabilitata alla volta solo se PF>1.1 su paper

---

## 9. Comandi utili

```bash
pip install pyyaml httpx pytest pydantic pytest-asyncio
python scripts/strategy_eval.py --symbol BTCUSDT --bars 1500
python scripts/strategy_eval.py --symbol ETHUSDT --bars 1500
set PYTHONPATH=shared_config && python -m pytest tests/test_validate_config.py -q
```

---

## 10. Conclusione

Il codice strategico precedente era **non pronto per live** (bias EMA, filtri rotti, assenza edge/fee). La nuova stack è **architetturalmente corretta** e allineata allo scalping crypto realistico, ma la **profittabilità non è dimostrata** sul proxy offline attuale.

**Priorità assolute rispettate:** risk management e robustezza > backtest “belli”.  
**Prossimo passo non negoziabile:** paper trading su WebSocket Binance reali prima di qualsiasi riabilitazione OBI / Bollinger / VolExpansion.
