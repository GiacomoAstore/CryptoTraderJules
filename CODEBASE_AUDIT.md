# CODEBASE AUDIT — CryptoScalper Pro (CryptoTraderJules)

**Data:** 2026-06-02  
**Scope:** Scansione completa del repository `CryptoTraderJules`  
**Destinazione:** [CODEBASE_AUDIT.md](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/CODEBASE_AUDIT.md)

---

## 1. STACK TECNICO

### 1.1 Linguaggi e Framework
*   **Backend (Microservizi):** Python (FastAPI, Asyncio).
*   **Frontend (Dashboard):** React 19, Vite 8 (SPA con caricamento statico tramite Nginx).
*   **Database principale:** TimescaleDB (PostgreSQL 14) — usato per la persistenza time-series (ticks, OHLCV) e relazionale (orders, trades, positions).
*   **Cache / Message Broker:** Redis 7 (usato come Pub/Sub per lo streaming dei tick in tempo reale, code di comandi, memorizzazione temporanea dello stato e coordinamento dei microservizi).
*   **Orchestrazione:** Docker Compose (8 servizi principali configurati, più 3 servizi opzionali/utility).

### 1.2 ORM e Accesso Dati
*   **ORM:** **Nessuno**. Il backend utilizza query SQL raw scritte manualmente ed eseguite tramite il client asincrono `asyncpg`.
*   **Migrazioni:** **Alembic** (con driver `asyncpg`). Il database viene inizializzato con una singola migrazione baseline (`0001_baseline`), con script di riparazione legacy per database pre-esistenti.
*   **Database Helper:** Le query e le operazioni di inserimento/selezione sono incapsulate nel modulo di repository ([repository.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/api_gateway/repository.py)).

### 1.3 Librerie Principali
*   **Autenticazione:** `PyJWT` (JWT con firma HS256) + `passlib` (bcrypt per hashing delle password).
*   **HTTP Client:** `httpx` (usato per le richieste asincrone verso la REST API di Crypto.com e Telegram Bot API).
*   **Rate Limiting:** `slowapi` (implementa il rate limiting sugli endpoint del gateway).
*   **Config Validation:** `pydantic` v2 (usato per caricare e validare lo schema di `config.yaml`).
*   **Data Science / Indicators:** Calcoli matematici ed indicatori finanziari custom scritti in Python (tramite `Decimal` per evitare problemi di virgola mobile).
*   **Charting Frontend:** `lightweight-charts` ^4.1.1 (TradingView charting library) utilizzata nella dashboard React per i grafici dei prezzi in tempo reale.
*   **LLM Integration:** Groq API SDK (utilizza il modello `llama-3.3-70b-versatile` per l'advisor di ottimizzazione).
*   **Telegram Integrations:** Invio di messaggi via API HTTP in formato MarkdownV2.

---

## 2. MODELLO DATI ATTUALE

Il database PostgreSQL/TimescaleDB è composto da 7 tabelle principali definite nel file single-source-of-truth [db_schema.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/api_gateway/db_schema.py).

### 2.1 Tabelle Esistenti

#### `ticks` ⏱️ (Hypertable compressa, partizionata su `time`)
Memorizza i singoli tick di mercato in tempo reale ricevuti tramite WebSocket Crypto.com.
*   `time` (TIMESTAMPTZ, NOT NULL)
*   `symbol` (VARCHAR(20), NOT NULL)
*   `price` (DOUBLE PRECISION, NOT NULL)
*   `volume` (DOUBLE PRECISION, nullable)
*   `side` (VARCHAR(10), nullable)
*   `bid_price` (DOUBLE PRECISION, nullable)
*   `ask_price` (DOUBLE PRECISION, nullable)
*   `bid_qty` (DOUBLE PRECISION, nullable)
*   `ask_qty` (DOUBLE PRECISION, nullable)
*   `timestamp_ms` (BIGINT, nullable)
*   *Indici:* `ix_ticks_symbol_time` su `(symbol, time DESC)`
*   *Policy:* Compressione dopo 2 ore (raggruppata per `symbol`), retention policy di 24 ore.

#### `trades` 📈 (Hypertable, partizionata su `time`)
Memorizza lo storico delle operazioni concluse (paper o reali).
*   `time` (TIMESTAMPTZ, NOT NULL) - **Chiave Primaria (1/2)**
*   `id` (VARCHAR(50), NOT NULL) - **Chiave Primaria (2/2)**
*   `symbol` (VARCHAR(20), NOT NULL)
*   `side` (VARCHAR(10), NOT NULL)
*   `entry_price` (DOUBLE PRECISION, NOT NULL)
*   `exit_price` (DOUBLE PRECISION, NOT NULL)
*   `quantity` (DOUBLE PRECISION, NOT NULL)
*   `pnl_usdt` (DOUBLE PRECISION, NOT NULL)
*   `pnl_pct` (DOUBLE PRECISION, NOT NULL)
*   `open_time` (TIMESTAMPTZ, NOT NULL)
*   `close_time` (TIMESTAMPTZ, NOT NULL)
*   `strategy_name` (VARCHAR(50), NOT NULL)
*   `stop_loss_price` (DOUBLE PRECISION, nullable)
*   `take_profit_price` (DOUBLE PRECISION, nullable)
*   `close_reason` (VARCHAR(50), NOT NULL)
*   `fee` (DOUBLE PRECISION, DEFAULT 0.0)
*   `ab_variant` (VARCHAR(1), NOT NULL, DEFAULT 'A')
*   *Indici:* `ix_trades_symbol` su `(symbol)`, `ix_trades_strategy` su `(strategy_name)`

#### `positions` 📌
Mantiene lo stato delle posizioni aperte correnti.
*   `symbol` (VARCHAR(20), NOT NULL) - **Chiave Primaria (1/2)**
*   `ab_variant` (CHAR(1), NOT NULL) - **Chiave Primaria (2/2)**
*   `entry_time` (TIMESTAMPTZ, NOT NULL)
*   `entry_price` (DECIMAL, NOT NULL)
*   `quantity` (DECIMAL, NOT NULL)
*   `side` (VARCHAR(10), NOT NULL)
*   `stop_loss` (DECIMAL, nullable)
*   `take_profit` (DECIMAL, nullable)

#### `orders` 🧾
Storico degli ordini inviati all'exchange.
*   `id` (UUID, PRIMARY KEY)
*   `time` (TIMESTAMPTZ, NOT NULL, DEFAULT NOW())
*   `symbol` (VARCHAR(20), NOT NULL)
*   `side` (VARCHAR(10), NOT NULL)
*   `price` (DECIMAL, NOT NULL)
*   `quantity` (DECIMAL, NOT NULL)
*   `status` (VARCHAR(20), NOT NULL)
*   `strategy` (VARCHAR(50), nullable)
*   `ab_variant` (CHAR(1), nullable)
*   `exchange_order_id` (VARCHAR(100), nullable)

#### `order_commands` ⚙️
Registra le richieste di inserimento ordine inviate dall'engine di rischio verso l'executor.
*   `id` (VARCHAR(50), PRIMARY KEY)
*   `timestamp` (TIMESTAMPTZ, NOT NULL, DEFAULT NOW())
*   `symbol` (VARCHAR(20), NOT NULL)
*   `type` (VARCHAR(10), NOT NULL)
*   `price` (DOUBLE PRECISION, NOT NULL)
*   `quantity` (DOUBLE PRECISION, NOT NULL)
*   `strategy` (VARCHAR(50), NOT NULL)
*   `status` (VARCHAR(20), NOT NULL)

#### `daily_performance` 📊
Tabella di aggregazione per le performance giornaliere del bot.
*   `date` (DATE, PRIMARY KEY)
*   `total_pnl` (DOUBLE PRECISION, NOT NULL, DEFAULT 0.0)
*   `win_count` (INTEGER, NOT NULL, DEFAULT 0)
*   `loss_count` (INTEGER, NOT NULL, DEFAULT 0)
*   `win_rate` (DOUBLE PRECISION, NOT NULL, DEFAULT 0.0)
*   `max_drawdown` (DOUBLE PRECISION, NOT NULL, DEFAULT 0.0)
*   `sharpe_ratio` (DOUBLE PRECISION, NOT NULL, DEFAULT 0.0)

#### `ohlcv` 🕯️ (Hypertable, partizionata su `time`)
Aggregazione delle candele di mercato (1 minuto).
*   `time` (TIMESTAMPTZ, NOT NULL) - **Chiave Primaria (1/2)**
*   `symbol` (VARCHAR(20), NOT NULL) - **Chiave Primaria (2/2)**
*   `open` (DOUBLE PRECISION, NOT NULL)
*   `high` (DOUBLE PRECISION, NOT NULL)
*   `low` (DOUBLE PRECISION, NOT NULL)
*   `close` (DOUBLE PRECISION, NOT NULL)
*   `volume` (DOUBLE PRECISION, NOT NULL)

### 2.2 Relazioni tra Entità
Non ci sono vincoli di Foreign Key espliciti definiti a livello di database per ottimizzare le performance di inserimento ad alta frequenza. Le relazioni sono logiche e gestite dall'applicazione:

*   **`ticks` ➔ `positions` / `trades`:** Il flusso dei tick viene utilizzato per aggiornare i prezzi correnti delle posizioni e per calcolare gli SL/TP.
*   **`order_commands` ➔ `orders`:** L'executor riceve un `order_command` ed emette un `order` sul DB.
*   **`positions` ➔ `trades`:** Quando una posizione viene chiusa dall'executor, il record viene rimosso da `positions` e inserito in `trades` con il PnL finale.
*   **`trades` ➔ `daily_performance`:** Il servizio `reporter` aggrega periodicamente i record di `trades` per popolare la tabella `daily_performance`.

---

## 3. API ENDPOINTS IMPLEMENTATI

Gli endpoint esposti dall'API Gateway ([api_gateway/main.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/api_gateway/main.py)) sono i seguenti:

### 3.1 Autenticazione e Utility
*   `GET /`: Ritorna lo stato base del servizio e la revisione del DB.
*   `GET /health/db`: Verifica l'allineamento dello schema del DB richiamando `verify_db_schema.py`.
*   `GET /api/health`: Healthcheck esteso (connessione Redis, stato circuit breaker, battito cardiaco ingestion).
*   `POST /api/login`: Endpoint OAuth2. Riceve username/password (admin) e rilascia un JWT valido per 24 ore.

### 3.2 Portfolio e Saldi
*   `GET /api/portfolio/real`: Recupera il saldo reale da Crypto.com (solo asset non vuoti) e valorizza il totale in USDT usando i prezzi in tempo reale su Redis.

### 3.3 Configurazione e Controllo Bot
*   `GET /api/symbols`: Ritorna la lista dei simboli monitorati con l'ultimo prezzo registrato.
*   `GET /api/config`: Legge la configurazione di rischio corrente memorizzata su Redis.
*   `PUT /api/config`: Aggiorna la configurazione di rischio su Redis e notifica gli altri servizi tramite il canale pubsub `RELOAD_CONFIG`.
*   `POST /api/bot/start`: Imposta lo stato del bot su "running" in Redis.
*   `POST /api/bot/stop`: Imposta lo stato del bot su "stopped" in Redis.
*   `POST /api/bot/toggle`: Endpoint legacy che accetta un booleano per avviare o arrestare il bot.
*   `GET /api/bot/status`: Ritorna lo stato attuale del bot, del circuit breaker e il bilancio di paper trading.

### 3.4 Operazioni e Storico Trade
*   `GET /api/trades`: Ritorna la lista degli ultimi trade registrati sul database.
*   `GET /api/trades/{symbol}`: Ritorna i trade filtrati per un simbolo specifico.
*   `GET /api/positions`: Mostra le posizioni attualmente aperte in formato JSON.

### 3.5 Database Explorer (per la dashboard)
*   `GET /api/db/tables`: Ritorna la lista di tutte le tabelle pubbliche nel DB.
*   `POST /api/db/table/{table_name}`: Esegue una query dinamica sulla tabella indicata applicando limiti e filtri SQL (con protezione da SQL Injection).

### 3.6 Performance e Report
*   `GET /api/performance/summary`: Restituisce le metriche aggregate (PnL totale, win rate, Sharpe ratio, numero di trade).
*   `GET /api/performance/daily`: Placeholder (stub che ritorna una lista vuota).
*   `POST /api/report/send`: Invia manualmente un report di paper trading del mattino tramite Telegram pubblicando un comando in Redis.

### 3.7 WebSocket
*   `WS /ws/live?token={JWT}`: Connessione WebSocket autenticata per il frontend. Trasmette in tempo reale i tick, i segnali generati, i trade eseguiti e gli aggiornamenti del portfolio (ogni 1s).

---

## 4. FUNZIONALITÀ IMPLEMENTATE (Stato E2E)

### 4.1 Completamente Funzionanti
*   **Ingestione Dati Real-time:** Connessione stabile via WS a Crypto.com per trade, ticker e book snapshot. Salvataggio in Redis per i dati real-time e scrittura in batch bufferizzata su TimescaleDB.
*   **Generazione Segnali e Consensus:** `SignalEngine` monitora i tick e calcola gli indicatori. Supporta A/B testing con parametri diversi per le varianti A e B. Calcola il consensus su più strategie (EMA, Momentum, ecc.) ed applica filtri di mercato (spread, chop filter, ATR, edge vs fee).
*   **Gestione del Rischio (Paper Mode):** Controllo esposizione massima, perdita massima giornaliera, circuit breaker automatico dopo 3 perdite consecutive, stop-loss e take-profit dinamici basati su ATR, e spostamento dello stop a breakeven una volta raggiunto 1x ATR di profitto.
*   **Esecuzione Ordini (Paper Mode):** Simulatore completo che gestisce l'intero ciclo di vita degli ordini limit (inserimento, fill a toccamento prezzo, scadenze ordine temporali) e aggiorna i bilanci virtuali (varianti A e B).
*   **Dashboard di Controllo:** Interfaccia web React per avviare/arrestare il bot, visualizzare posizioni aperte, storico trade, configurazioni di rischio e consultare le tabelle grezze del DB.
*   **Notifiche Telegram:** Telegram alerter per trade aperti/chiusi, scatti del circuit breaker e report periodici.

### 4.2 Parzialmente Implementate / Da Completare
*   **Live Trading (Crypto.com Real):** I client per Crypto.com REST (`cryptocom_rest.py`) e l'engine live (`live_engine.py`) sono implementati come scheletri/scaffold per la Fase 2. L'inserimento degli ordini reali su Crypto.com è esplicitamente bloccato da una `NotImplementedError` per sicurezza (Phase 1 gate).
*   **LLM Advisor:** Integrato con Groq API. Genera file di advisory in Markdown (`reports/llm_advisory_*.md`) analizzando le metriche storiche dei trade, ma non applica direttamente le configurazioni in modo autonomo per sicurezza durante la Fase 1.
*   **Backtester:** Il servizio `backtester` è uno stub non implementato (ritorna "not_implemented"). Tuttavia, lo script offline [scripts/strategy_eval.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/scripts/strategy_eval.py) permette la valutazione storica di base simulando spread, fee e slippage.
*   **Grafici Dashboard:** La libreria `lightweight-charts` è configurata nel frontend ma i grafici mostrano solo linee di prezzo in tempo reale, senza integrazione dello storico OHLCV.

---

## 5. STATO SaaS & CONFRONTO CON IL PIANO

> [!WARNING]
> **Nota Importante di Discrepanza:** La codebase corrente rappresenta esclusivamente un **bot di trading algoritmico di criptovalute micro-scalping**. Nel repository **non è presente alcun file denominato "SAAS IMPLEMENTATION PLAN"**, né vi sono riferimenti a concetti SaaS come `subscriptions`, `workspaces`, `tenants`, o integrazioni con Stripe. Inoltre, **non esistono entità come `Wedding` o `Fornitore`** (probabilmente ereditate da un template o prompt di un altro progetto). 

Tuttavia, ipotizzando una migrazione di questo Trading Bot verso un modello SaaS multi-utente, ecco l'analisi dello stato attuale:

### 5.1 Cosa del Piano SaaS è già implementato (anche parzialmente)
*   **Autenticazione API/WS:** L'infrastruttura utilizza già JWT per proteggere gli endpoint e la connessione WebSocket. Questa logica è facilmente estendibile per supportare account utente generici invece del singolo utente `admin` hardcoded.
*   **Separazione delle varianti (A/B testing):** Il database e l'engine supportano già la separazione logica tra varianti A e B (tramite la colonna `ab_variant`). Questo design facilita la separazione dei dati per utente/strategia.

### 5.2 Cosa è completamente assente per un modello SaaS
*   **Registrazione Utente e Gestione Profili:** Manca una tabella `users` nel DB. Non c'è un onboarding flow, né il recupero password o la verifica email.
*   **Isolamento dei Dati (Multi-tenancy):** Non esiste la colonna `tenant_id` o `user_id` nelle tabelle chiave (`trades`, `positions`, `orders`, `order_commands`). Un utente loggato vedrebbe i trade e le posizioni di tutti gli altri.
*   **Gestione delle API Key Crypto.com per utente:** Attualmente, le chiavi API di Crypto.com sono caricate globalmente tramite il file `.env` per l'intero sistema. Per un SaaS, le chiavi devono essere memorizzate nel DB per ciascun utente in modo crittografato.
*   **Piani e Abbonamenti (Billing):** Nessuna integrazione con Stripe o altri gateway di pagamento. Nessun concetto di piano "Free vs Pro" o limiti sul numero di posizioni/simboli basati sull'abbonamento.
*   **Isolamento delle risorse di esecuzione:** Il bot esegue le strategie in thread/loop globali. In un SaaS, l'ingestione e la generazione di segnali dovrebbero essere isolate o quantomeno filtrate per le chiavi API di ciascun utente.

### 5.3 Implementato in modo incompatibile con un modello SaaS
*   **Stato in memoria dei servizi `risk_manager` e `order_executor`:** Le posizioni attive e le metriche di rischio giornaliere sono parzialmente gestite in-memory (es. `self.open_positions` in dizionari Python). In un ambiente multi-tenant con migliaia di utenti, questo approccio causerebbe il crash dei container per esaurimento memoria e perdita completa di dati in caso di riavvio. Tutto lo stato deve essere persistito su Redis/DB con partizionamento per utente.
*   **Vincoli di unicità sul database:** La tabella `positions` ha una chiave primaria composta da `(symbol, ab_variant)`. Questo significa che nel sistema può esistere solo una posizione per `BTCUSDT` per la variante A. In un SaaS, la chiave primaria deve includere l'utente (`user_id, symbol, ab_variant`) per permettere a utenti diversi di avere posizioni sullo stesso simbolo contemporaneamente.

---

## 6. CAMPI/ENTITÀ DA MIGRARE O RIMUOVERE

Dato che le tabelle `User`, `Wedding`, `Fornitore` e i relativi campi di `subscription` o `workspaceId` **non esistono** in questa codebase, l'analisi si concentra sulla ristrutturazione del modello dati del trading bot per consentire la transizione a SaaS.

### 6.1 Campi e Tabelle da Aggiungere / Migrare

#### 1. Nuova tabella `users`
È necessario creare una tabella per gestire gli utenti del SaaS:
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    stripe_customer_id VARCHAR(100),
    subscription_status VARCHAR(50) DEFAULT 'inactive',
    subscription_plan VARCHAR(50) DEFAULT 'free'
);
```

#### 2. Nuova tabella `user_exchange_keys`
Per memorizzare in modo sicuro e crittografato le chiavi API di Crypto.com dei singoli utenti:
```sql
CREATE TABLE user_exchange_keys (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exchange_name VARCHAR(50) NOT NULL DEFAULT 'cryptocom',
    api_key VARCHAR(255) NOT NULL,
    api_secret_encrypted TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, exchange_name)
);
```

#### 3. Migrazione della tabella `positions`
La chiave primaria corrente deve essere estesa per includere l'identificativo dell'utente:
*   *Stato attuale:* `PRIMARY KEY (symbol, ab_variant)`
*   *Azione:* Migrare a `PRIMARY KEY (user_id, symbol, ab_variant)` dove `user_id` è una Foreign Key verso la tabella `users`.

#### 4. Migrazione della tabella `trades`
Aggiungere la colonna `user_id` per associare lo storico delle transazioni all'utente proprietario:
*   *Azione:* Aggiungere `user_id UUID NOT NULL` (e includerlo nell'indice `ix_trades_user_symbol`).

#### 5. Migrazione della tabella `orders` e `order_commands`
Associare ogni ordine inserito o comando inviato all'utente specifico:
*   *Azione:* Aggiungere `user_id UUID NOT NULL` a entrambe le tabelle.

#### 6. Migrazione dei dati in Redis
Tutte le chiavi globali usate da `risk_manager` e `order_executor` devono includere un namespace basato sull'ID dell'utente:
*   `bot:status` ➔ `user:{user_id}:bot:status`
*   `risk:daily_loss` ➔ `user:{user_id}:risk:daily_loss`
*   `risk:circuit_breaker` ➔ `user:{user_id}:risk:circuit_breaker`
*   `positions:open` ➔ `user:{user_id}:positions:open`

---

### 6.2 File Temporanei o Legacy da Rimuovere

Per pulire la codebase e prepararla alla produzione, si raccomanda la rimozione dei seguenti file di debug e test temporanei che sporcano la radice del progetto:

1.  **Script di debug dell'ATR:**
    *   [scratch_atr.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/scratch_atr.py)
    *   [scratch_5m_atr.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/scratch_5m_atr.py)
2.  **Altri script scratch:**
    *   [scratch_duration.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/scratch_duration.py)
    *   [scratch_edge.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/scratch_edge.py)
    *   [scratch_trade_watch.sh](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/scratch_trade_watch.sh)
    *   [tmp_breakout_debug.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/tmp_breakout_debug.py)
3.  **Log di deployment:**
    *   I file presenti in `deploy_logs/*.log` (`signal_engine.log`, `signals.log`, etc.) non dovrebbero essere inclusi nel repository git e devono essere aggiunti a `.gitignore`.
4.  **Codice Obsoleto / Legacy:**
    *   [api_gateway/repair_legacy_alembic.py](file:///c:/Users/Utente/Downloads/cryptoTraderJules/CryptoTraderJules/api_gateway/repair_legacy_alembic.py): Script di riparazione una tantum per database molto vecchi (pre-baseline). Può essere rimosso una volta completata la migrazione definitiva al nuovo schema baseline `0001_baseline`.
