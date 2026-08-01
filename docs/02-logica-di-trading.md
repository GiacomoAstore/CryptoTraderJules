# Logica di Trading e Gestione del Rischio

## 1. Ciclo di Vita Completo di un Trade

```text
[ Data Ingestion ] 
       │ (Tick normalizzato su ticks:BTCUSDT)
       ▼
[ Signal Engine ] ──► (Verifica separazione EMA >= 3bps & Filtri Spread/Chop)
       │ (Segnale generato su signals:BTCUSDT)
       ▼
[ Risk Manager ]  ──► (Verifica Circuit Breaker, Max Loss $40 & Free Available Balance)
       │ (Calcolo Position Sizing: Notional <= Free Balance)
       │ (Ordine Approvato su approved_orders)
       ▼
[ Order Executor ] ──► (Piazzamento ordine Limit d'ingresso su pullback)
       │ (Order Filled -> Salva positions:active:BTCUSDT_A in Redis)
       ▼
[ LiveEngine reconciliation / PaperEngine ] ──► (Tracciamento Dual SL/TP)
       │
       ├─► (Prezzo >= TP -> Order Limit Filled -> Cancella positions:active)
       └─► (Prezzo <= SL -> Market Taker Executed -> Cancella positions:active)
```

---

## 2. Strategia Attiva: `EMACrossoverStrategy` (Varianti A & B)

La strategia identifica l'incrocio (crossover) tra una media mobile esponenziale veloce ed una lenta su dati a 1 minuto, Exigendo una separazione minima in basis points per eliminare i falsi segnali in fase di sideways.

### Confronto Parametri Varianti A & B (`config.yaml`):

| Parametro | **Variante A** | **Variante B** | Note |
| :--- | :--- | :--- | :--- |
| **EMA Veloce (`fast_period`)** | **EMA 8** | **EMA 5** | Velocità di reazione al prezzo |
| **EMA Lenta (`slow_period`)** | **EMA 21** | **EMA 13** | Filtro del trend principale |
| **Separazione Minima (`min_separation_bps`)**| **3 bps** | **5 bps** | Soglia minima di ampiezza crossover |
| **Spread Massimo (`max_spread_bps`)** | **10 bps** | **8 bps** | Filtro di validazione dello spread |
| **Book Qty Minima (`min_book_qty`)** | **0.01** | **0.02** | Liquidità minima a libro d'ordini |
| **Peso Strategia (`weight`)** | **1.2** | **1.2** | Peso nell'aggregazione segnali |

* **Tipi d'Ordine Utilizzati**:
  * **Entry**: Ordine Limit su pullback (Maker Fee = 7.5 bps o 0.0 bps con Staking CRO).
  * **Take Profit**: Ordine Limit condizionale (Maker Fee = 7.5 bps o 0.0 bps con Staking CRO).
  * **Stop Loss**: Ordine Market a mercato (Taker Fee = 10–15 bps o 8.8 bps con Staking CRO).

---

## 3. Gestione del Rischio e Position Sizing

### A) Circuit Breaker
* **Perdita Massima Giornaliera**: Cappata a **$40.00 USDT**. Al raggiungimento della soglia, il bot arresta qualsiasi nuova apertura per la giornata.
* **Consecutive Losses Pause**: Dopo **3 perdite consecutive**, il sistema imposta lo stato `status: open` nell'hash Redis `risk:circuit_breaker` ed attiva la pausa operativa inviando una notifica sul canale `phase1:urgent`.

### B) Position Sizing con *Free Available Balance* Netto
Il dimensionamento della posizione si basa sulla formula del rischio percentuale fisso ($0.8\%$ per trade) sul capitale disponibile netto:

$$\text{Rischio in USD} = \text{Total Balance} \times 0.8\%$$
$$\text{Calculated Notional} = \frac{\text{Rischio in USD}}{\left(\frac{\text{SL Distance}}{P_{\text{entry}}}\right)}$$

Per evitare di inviare ordini non coperti dal saldo reale Spot (senza leva), l'esposizione notionale d'ingresso viene rigorosamente cappata al **Saldo Netto Libero Disponibile**:

$$\text{Free Available Balance} = \text{Total Balance} - \sum_{\text{pos} \in \text{OpenPositions}} (P_{\text{entry}} \times Q)$$
$$\text{Exposure}_{\text{finale}} = \min\left( \text{Calculated Notional}, \quad \text{max\_exposure\_per\_symbol\_usdt}, \quad \text{Free Available Balance} \right)$$

* **Reject `LOW_FUNDS`**: Se $\text{Exposure}_{\text{finale}} < \$1.00\text{ USDT}$ (min\_notional d'exchange), il segnale viene scartato dal `risk_manager` registrando `risk:stats:rejected_low_funds` ed inviando una notifica su `alerts:telegram` con `Reason: LOW_FUNDS`.

---

## 4. Riconciliazione e Stop Loss Nativo (Livello 2)

* **Perché esiste il Livello 2**: Sugli exchange crypto REST/WebSocket, se una posizione viene chiusa direttamente via Take Profit o Stop Loss lato exchange, l'engine locale deve riconciliare immediatamente lo stato in memoria e cancellare l'ordine condizionale opposto (SL o TP).
* **Meccanismo di Protezione**:
  * **Dual SL/TP Tracking**: `live_engine.py` traccia sia l'ordine TP che lo SL condizionale per ciascuna posizione aperta.
  * **Orphan Order Cleanup**: Se il TP viene eseguito, l'engine esegue la cancellazione dello SL rimasto orfano con fino a 2 tentativi ed un allarme critico su `system:alerts` (`ORPHAN_ORDER_CLEANUP_FAILED`) se l'exchange non risponde.
  * **Opposite Order Resizing**: Se una posizione viene parzialmente eseguita (`PARTIALLY_FILLED`), l'ordine opposto viene automaticamente ridimensionato ed aggiornato per proteggere l'esatto ammontare residuo (`POSITION_UNPROTECTED_AFTER_RESIZE`).
