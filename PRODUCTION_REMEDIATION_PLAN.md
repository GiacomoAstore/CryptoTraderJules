# Production Hardening & Remediation Plan: CryptoTraderJules

Questo documento delinea i passaggi necessari per trasformare il prototipo attuale in un sistema di trading production-grade capace di operare con capitale reale in sicurezza.

## 1. Vulnerabilità Critiche

| Vulnerabilità | Rischio | Impatto Economico | Probabilità | Remediation |
| :--- | :--- | :--- | :--- | :--- |
| **Stato Volatile (In-Memory)** | **CRITICO** | Perdita totale controllo posizioni | **CERTA** (al primo restart) | Migrazione stato su PostgreSQL + Redis Hash. |
| **Idempotenza Assente** | **ALTO** | Ordini duplicati / Loop esecuzione | Media | Redis `SETNX` per ogni `command_id`. |
| **WebSockets non Protetti** | **ALTO** | Esfiltrazione segnali / Front-running | Alta | JWT Auth obbligatorio su connessione WS. |
| **Floating Point Precision** | **MEDIO** | Errori arrotondamento PnL / Size | Alta | Sostituzione `float` con `Decimal`. |
| **Secrets in Cleartext** | **ALTO** | Furto API Keys / Fondi | Bassa-Media | Integrazione Vault o Env criptate. |

---

## 2. Trading Engine: Hardening & Redesign

### Lifecycle Ordini e Idempotenza
Attualmente, se il broker Redis invia un messaggio due volte (at-least-once delivery), l'executor apre due posizioni. 

**Proposta:** Implementare un "Execution Gateway" persistente.

```python
# pseudo-codice per Order Executor sicuro
async def handle_order(cmd):
    # 1. Idempotency Check (Persistent)
    if not await redis.setnx(f"lock:order:{cmd.id}", "1"):
        return # Già processato

    # 2. Database Reservation (State: PENDING)
    await db.save_order(cmd, status='PENDING')

    # 3. Exchange Execution con Timeout & Retry Limit
    try:
        execution_res = await exchange.create_order(cmd.symbol, cmd.qty, cmd.price)
        # 4. Success State update
        await db.update_order(cmd.id, status='FILLED', exchange_id=execution_res.id)
    except Exception as e:
        # 5. Handle Network Failure vs Logic Failure
        await db.update_order(cmd.id, status='ERROR', reason=str(e))
        # TRIGGER RECONCILIATION: verifica se l'ordine è passato comunque
```

### Riconciliazione (Exchange Reconciliation)
È fondamentale un loop di riconciliazione che gira ogni minuto:
1. Scarica posizioni aperte da Binance REST API.
2. Confronta con le posizioni segnate nel Database locale.
3. Se discrepanza > `tolerance`, ferma il bot e invia alert critico.

---

## 3. Risk Management: Sicurezza dei Fondi

### Circuit Breaker Persistente
Il `daily_pnl` deve essere salvato su Redis con chiave `pnl:YYYY-MM-DD`. In questo modo, se il servizio `risk_manager` crasha, al riavvio ricarica il drawdown reale della giornata.

### Emergency Kill Switch
Implementare un comando `SYSTEM_HALT` che:
1. Invia `CANCEL_ALL_ORDERS` all'exchange.
2. Invia `CLOSE_ALL_POSITIONS` (Market Orders).
3. Disabilita il `signal_engine` impostando un flag in Redis.

---

## 4. Resilienza Realtime

### Heartbeat e Stale Data Detection
Il `data_ingestion` deve pubblicare un heartbeat. Il `signal_engine` deve scartare i tick se il timestamp è più vecchio di 500ms rispetto al tempo di sistema (scarto di latenza).

```python
if (current_time_ms - tick['timestamp_ms']) > MAX_LATENCY_ALLOWED:
    logger.error("STALE_TICK_DETECTED: Ignoring data stream")
    continue
```

---

## 5. Precisione Finanziaria

**DIVIETO ASSOLUTO DI FLOAT.**
Ogni calcolo di PnL, commissioni e size deve usare `decimal.Decimal`.

```python
from decimal import Decimal, ROUND_DOWN

def calculate_qty(balance, risk_pct, price, sl_dist):
    balance = Decimal(str(balance))
    risk_amount = balance * Decimal(str(risk_pct))
    qty = risk_amount / Decimal(str(sl_dist))
    # Arrotondamento basato sulla precisione dell'exchange (es. 0.001)
    return qty.quantize(Decimal('0.001'), rounding=ROUND_DOWN)
```

---

## 6. Checklist Go-Live

- [ ] **Persistenza:** Le posizioni sopravvivono a `docker-compose restart`.
- [ ] **Auth:** Tutte le API e WS richiedono JWT valido.
- [ ] **Secrets:** Le chiavi API di Binance non sono presenti in alcun file git o log.
- [ ] **Precisione:** Test unitari confermano 0 errori di arrotondamento su 1 milione di trade.
- [ ] **Reconciliation:** Il bot rileva automaticamente un ordine chiuso manualmente su Binance.
- [ ] **Kill Switch:** Testato con successo in ambiente di test.

---

## 7. Verdetto Finale

| Criterio | Valutazione |
| :--- | :--- |
| **Readiness Score** | **3 / 10** |
| **Rischio Perdita Fondi** | **ESTREMO** (Causa crash/riavvio) |
| **Stabilità Realtime** | **MEDIOCRE** (Nessuna gestione stale data) |
| **Rischio Exploit** | **ALTO** (WS pubblico) |

### Classifica Finale: **NON UTILIZZABILE CON SOLDI REALI**

**Nota:** Il sistema è un ottimo prototipo architetturale, ma manca della "corazza" necessaria per gestire la volatilità e i fallimenti infrastrutturali tipici del mercato crypto.
