# Architettura Tecnica e Mappatura Servizi

## 1. Mappatura dei Microservizi

### `data_ingestion`
* **Responsabilità**: Cattura ed armonizzazione dei dati di mercato in tempo reale direttamente dalle WebSocket ed API pubbliche di **Crypto.com Exchange**.
* **Entry Point**: `data_ingestion/main.py`
* **Dipendenze & Canali**: Connesso a `wss://stream.crypto.com/exchange/v1/market`, sottoscrive i canali `ticker.{symbol}`, `trade.{symbol}` e `book.{symbol}.10` (profondità 10), e pubblica su Redis `ticks:{symbol}`. Le funzioni di conversione simbolo `binance_to_cryptocom_symbol` sono puri helper per retrocompatibilità interna delle chiavi.
* **File Principali**:
  * `data_ingestion/main.py`: Connessione WebSocket e normalizzazione tick.
  * `data_ingestion/tick_writer.py`: Persistenza in batch su TimescaleDB.

### `signal_engine`
* **Responsabilità**: Generazione dei segnali di trading sulla base degli indicatori tecnici e dei filtri di mercato.
* **Entry Point**: `signal_engine/main.py`
* **Dipendenze & Canali**: Sottoscrive `ticks:{symbol}`, applica i filtri (`market_filters.py`), pubblica su `signals:{symbol}`.
* **File Principali**:
  * `signal_engine/strategy.py`: Implementazione di `EMACrossoverStrategy`, `MomentumBurstStrategy`, `OrderBookImbalanceStrategy` (disattivata), `MicroStructureBreakoutStrategy` (disattivata).
  * `signal_engine/market_filters.py`: Filtri di spread massimo, atr minimo e chop ratio.
  * `signal_engine/indicators.py`: Calcolo vettoriale di EMA, ATR, RSI, ChOP.

### `risk_manager`
* **Responsabilità**: Validazione del rischio, Circuit Breaker (perdita max giornaliera e 3 perdite consecutive), Position Sizing e cap sul *Free Available Balance*.
* **Entry Point**: `risk_manager/main.py`
* **Dipendenze & Canali**: Sottoscrive `signals:{symbol}`, pubblica su `approved_orders`, `alerts:telegram` e `phase1:urgent`.
* **File Principali**:
  * `risk_manager/main.py`: Logica del circuit breaker (`trigger_circuit_breaker`), `get_free_available_balance()`, `LOW_FUNDS` rejection check.

### `order_executor`
* **Responsabilità**: Esecuzione degli ordini in modalità Paper Trading (`PaperEngine`) e Live (`LiveEngine`), riconciliazione periodica a 15s (Livello 2), e rimozione degli ordini orfani.
* **Entry Point**: `order_executor/main.py`
* **Dipendenze & Canali**: Sottoscrive `approved_orders`, legge/scrive `positions:active:*`, pubblica su `executed_trades`, `order_events` e `system:alerts`.
* **File Principali**:
  * `order_executor/main.py`: Dispatcher principale ed engine di simulazione Paper Trading.
  * `order_executor/live_engine.py`: Engine live reale con reconciliation loop a 15s, dual SL/TP tracking e gate `NotImplementedError`.
  * `order_executor/cryptocom_rest.py`: Client REST con autenticazione HMAC SHA256 di Crypto.com (`private/user-balance`).
  * `order_executor/exchange_rules.py`: Validazione `min_qty` e `min_notional` (`get-instruments`).

### `api_gateway` & `dashboard`
* **Responsabilità**: Gateway REST/WebSocket FastAPI per servire la dashboard React con metriche in tempo reale.
* **Entry Point**: `api_gateway/main.py`

### `reporter` & `telegram_alerter`
* **Responsabilità**: Generazione report giornalieri e trasmissione notifiche su Telegram per esecuzioni o scarti di rischio.

### `llm_optimizer`
* **Responsabilità**: Analisi periodica advisor (read-only) delle performance di trading (`llm_optimizer/advisor.py`).

---

## 2. Schema delle Tabelle TimescaleDB

* **`orders`**: Storico completo degli ordini inviati/ricevuti (`id`, `symbol`, `side`, `price`, `quantity`, `status`, `strategy`, `ab_variant`, `created_at`).
* **`positions`**: Tabella dello stato attivo delle posizioni aperte (`symbol`, `ab_variant`, `entry_time`, `entry_price`, `quantity`, `side`, `stop_loss`, `take_profit`).
* **`trades`**: Registro definitivo dei trade conclusi con PnL netto e fee sostenute (`id`, `symbol`, `side`, `entry_price`, `exit_price`, `quantity`, `pnl_usdt`, `pnl_bps`, `close_reason`, `close_time`).

---

## 3. Canali Redis Pub/Sub e Formato Messaggi

* **`ticks:{symbol}`**: Payload tick normalizzato (`{"symbol": "BTCUSDT", "price": "65000.0", "bid_price": "64999.5", "ask_price": "65000.5", "timestamp_ms": 1772500000000}`).
* **`signals:{symbol}`**: Segnale emesso da signal engine (`{"symbol": "BTCUSDT", "type": "BUY", "price": "65000.0", "strategy": "EMACrossoverStrategy", "ab_variant": "A"}`).
* **`approved_orders`**: Ordine approvato da RiskManager con parametri SL/TP/Sizing calcolati (`{"command_id": "...", "symbol": "BTCUSDT", "type": "BUY", "price": "65000.0", "quantity": "0.0015", "stop_loss_price": "64770.0", "take_profit_price": "65455.0"}`).
* **`executed_trades`**: Notifica di chiusura trade (`{"symbol": "BTCUSDT", "pnl_usdt": 1.25, "pnl_bps": 19.2, "close_reason": "TP"}`).
* **`system:alerts`**: Alert di errore/critici inviati da LiveEngine (`{"level": "error", "message": "Live trading not enabled — Phase 2 gate required", "symbol": "BTC_USDT"}`).
* **`phase1:urgent`**: Notifiche urgenti di apertura Circuit Breaker (`{"event": "circuit_breaker_open", "message": "Circuit breaker APERTO..."}`).
* **`alerts:telegram`**: Notifiche di scarto filtri ed allerte telegram (`{"event": "risk_filter", "message": "Reason: LOW_FUNDS..."}`).

---

## 4. Convenzioni di Naming delle Chiavi Redis

Il sistema adotta la convenzione rigida `dominio:entita:attributo`:

* **`paper:balance:A` / `paper:balance:B`**: Saldo USDT simulato in Paper Trading per la variante A e B.
* **`live:balance:usdt`**: Saldo totale USDT letto da Crypto.com via `private/user-balance`.
* **`live:balance:free_usdt`**: Saldo libero USDT disponibile letto dall'exchange.
* **`positions:active:{pos_key}`**: JSON rappresentante la posizione attiva in memoria (`{"symbol": "BTCUSDT", "ab_variant": "A", "entry_price": "65000.0", "quantity": "0.0015"}`).
* **`risk:circuit_breaker`**: Hash Redis indicante lo stato di pausa del circuit breaker (`{"status": "open", "reason": "...", "until": "..."}`).
