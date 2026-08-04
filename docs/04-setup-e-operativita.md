# Guida Operativa, Setup e Diagnostic Log

## 1. Avvio del Progetto in Locale (Docker Compose)

Il sistema viene eseguito tramite Docker Compose ed ambiente virtuale Python locale.

### Comandi Principali:
```bash
# 1. Avvio dei container di infrastruttura (TimescaleDB + Redis)
docker-compose up -d timescaledb redis

# 2. Verifica dello stato dei servizi
docker-compose ps

# 3. Avvio della suite completa dei microservizi
docker-compose up -d
```

### Limiti Risorse e Canali Ingestione (`data_ingestion`):
* **Memory Limit**: Il microservizio `data_ingestion` in `docker-compose.yml` è configurato con un limite di memoria esplicito a **512MB (`mem_limit: 512m`)** per prevenire saturazioni RAM dell'host durante l'operatività prolungata.
* **Canali WebSocket**: `data_ingestion` si connette a `wss://stream.crypto.com/exchange/v1/market` sottoscrivendo i canali `ticker.{symbol}`, `trade.{symbol}` e `book.{symbol}.10` (profondità 10, in quanto profondità diverse come 20 restituiscono l'errore `40003 Invalid depth`).

---

## 2. Variabili d'Ambiente Richieste (`.env`) & Buone Pratiche

Fare riferimento a `.env.example` per il template aggiornato. **Non inserire o committare mai chiavi reali o segreti su repository pubblici.**

### Parametri Chiave:
* `EXECUTION_MODE`: `PAPER` (default) o `LIVE` (bloccato da gate).
* `CRYPTOCOM_API_KEY`: API Key dell'exchange Crypto.com.
* `CRYPTOCOM_API_SECRET`: Secret Key HMAC per la firma delle richieste private.
* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Credentials per le allerte Telegram.
* `GROQ_API_KEY`: API Key per l'advisor read-only LLM.
* `REDIS_HOST`, `REDIS_PORT`: Parametri di connessione Redis (default `redis:6379`).
* `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`: Parametri di connessione a TimescaleDB.

### Buona Pratica Operativa (Rotazione Credenziali):
* Si raccomanda la **rotazione periodica di tutte le credenziali esterne** (API Key Crypto.com, Bot Token Telegram e Groq API Key). In caso di rotazione, aggiornare il file `.env` ed eseguire il riavvio dei microservizi interessati (`docker-compose up -d`).

---

## 3. Interpretazione dei Log e degli Eventi Redis

I microservizi notificano gli eventi di esecuzione e di rischio sui canali Redis dedicati e nei log di sistema:

### Eventi su `system:alerts` (OrderExecutor / LiveEngine):
* **Gate Live Bloccato** (`level: error`): Pubblicato da `LiveEngine` in `order_executor/live_engine.py` quando un ordine reale tenta l'invio ma viene intercettato dal gate di sicurezza.
  * *Payload*: `{"level": "error", "message": "Live trading not enabled — Phase 2 gate required", "symbol": symbol}`.
* **`ORPHAN_ORDER_CLEANUP_FAILED`** (`level: critical`): Pubblicato da `LiveEngine` se la cancellazione dell'ordine TP/SL opposto su Crypto.com fallisce dopo i retry. **Richiede intervento manuale**.
* **`POSITION_UNPROTECTED_AFTER_RESIZE`** (`level: critical`): Pubblicato se il ri-piazzamento dell'ordine condizionale opposto dopo un `PARTIALLY_FILLED` fallisce.

### Eventi su `phase1:urgent` e Hash Redis (RiskManager):
* **`circuit_breaker_open`** (`event: circuit_breaker_open`): Pubblicato su `phase1:urgent` quando il bot raggiunge la perdita massima giornaliera ($40) o 3 perdite consecutive.
  * *Stato Redis*: Imposta l'hash `risk:circuit_breaker` con `status: open`, `reason: reason`, `until: timestamp`.

### Eventi su `alerts:telegram` e Contatori Redis (RiskManager):
* **Scarto `LOW_FUNDS`**: Pubblicato quando il segnale viene scartato perché il saldo libero residuo è inferiore a $1.00 USDT (`min_notional`).
  * *Comportamento*: Esegue `logger.warning()`, incrementa la chiave Redis `risk:stats:rejected_low_funds:{hour_key}` e pubblica su `alerts:telegram` con `{"event": "risk_filter", "message": "Reason: LOW_FUNDS..."}`.
* **Scarto `Low Profitability`**: Pubblicato quando il TP atteso non copre le commissioni d'ingresso ed uscita.
  * *Comportamento*: Esegue `logger.warning()`, incrementa la chiave Redis `risk:stats:rejected_low_profit:{hour_key}` e pubblica su `alerts:telegram` con `{"event": "risk_filter", "message": "Reason: Low Profitability..."}`.

---

## 4. Procedura Operativa: Reset Capitale Paper Trading & Circuit Breaker

Qualora si desideri azzerare il capitale simulato di Paper Trading e sbloccare il Circuit Breaker per una nuova sessione di test:

### 1. Salva un backup cautelativo dello stato precedente su Redis:
```bash
docker exec cryptoscalper_redis redis-cli hset "risk:circuit_breaker:backup_pre_reset" status "open" reason "3 Consecutive Losses" timestamp "$(date +%s)"
```

### 2. Imposta il nuovo capitale simulato per le varianti A e B (es. $100 per variante, $200 totale) e sincronizza il capitale iniziale dinamico:
```bash
docker exec cryptoscalper_redis redis-cli set "paper:balance:A" "100.0"
docker exec cryptoscalper_redis redis-cli set "paper:balance:B" "100.0"
docker exec cryptoscalper_redis redis-cli set "paper:balance" "200.0"
docker exec cryptoscalper_redis redis-cli set "paper:starting_capital" "200.0"
```

### 3. Ripristina il Circuit Breaker e sblocca il Bot:
```bash
docker exec cryptoscalper_redis redis-cli hset "risk:circuit_breaker" status "closed" reason "manual_reset" timestamp "0"
docker exec cryptoscalper_redis redis-cli set "risk:consecutive_losses" "0"
docker exec cryptoscalper_redis redis-cli set "bot:status" "running"
```

---

## 5. Checklist Prima di un Eventuale Passaggio a Testnet

Prima di considerare qualunque rimozione del gate Live o passaggio a Testnet:

- [ ] **Riconciliazione Livello 2**: Verificata ed attiva con polling a 15s ed allarmi orfani pronti.
- [ ] **WebSocket Privato (Livello 1)**: Valutato ed integrato per ricevere i fill istantanei senza dipendere solo dal polling REST.
- [ ] **Strategia con Edge Dimostrato**: Decisione ed introduzione di un filtro di regime di trend per rendere l'expectancy netta significativamente positiva ($p < 0.05$).
- [ ] **Approvazione Esplicita**: Ottenuta l'approvazione formale dell'utente prima di qualsiasi chiamata API reale verso l'exchange.
