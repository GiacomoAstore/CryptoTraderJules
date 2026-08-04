# Trasparenza, Stato Attuale e Limiti Noti

## 1. Analisi di Redditività di `EMACrossoverStrategy`

L'analisi quantitativa condotta su un campione esteso di **30,000 candele REALI 1m di mercato (~21 giorni continuativi)** ha dimostrato in modo statisticamente inconfutabile l'assenza di redditività costante per la strategia `EMACrossoverStrategy` nella configurazione attuale:

### Risultati Numerici Definitivi su Dati Reali (30,000 Barre 1m):
* **Expectancy Media Netta ($\mu$)**: **$+1.63\text{ bps / trade}$** (con 50,000 CRO stake / 0% Maker fee).
* **Errore Standard ($\text{SE}$)**: **$4.76\text{ bps}$**.
* **Intervallo di Confidenza al 95%**: **$[-7.69\text{ bps}, +10.95\text{ bps}]$**.
* **Significatività Statistica ($p < 0.05$)**: **NO**. L'intervallo include ampiamente lo zero e valori negativi.

### Incoerenza Walk-Forward sulle 3 Finestre Settimanali:
* **Settimana 1 (12–19 Luglio)**: Expectancy $+3.28$ bps ($PF = 1.13$).
* **Settimana 2 (19–25 Luglio)**: Expectancy **-6.03 bps** ($PF = 0.78$) $\rightarrow$ **Perdita netta marcata**.
* **Settimana 3 (25 Lug–01 Agosto)**: Expectancy $+7.88$ bps ($PF = 1.37$).

**Conclusione Strategica**: La strategia oscilla tra guadagni e perdite marcate a seconda che il mercato sia in trend o in sideways/chop. Senza un filtro di regime di trend capace di disattivare il trading nelle fasi di consolidamento, l'expectancy non è distinguibile dal rumore campionario.

---

## 2. Perché il Gate Live è Bloccato e Requisiti per lo Sblocco

Il gate `NotImplementedError` in `order_executor/live_engine.py` e `order_executor/cryptocom_rest.py` **RESTA BLOCCATO**.

### Cosa Manca per l'Eventuale Sblocco:
1. **Decisione sulla Strategia**: Modifica ed introduzione di un filtro di regime di trend per evitare i periodi di chop o scelta di una strategia alternativa con edge dimostrato.
2. **WebSocket Privato (Livello 1)**: Implementazione del feed WebSocket privato per la ricezione istantanea dei fill dagli user data stream di Crypto.com, affiancando il loop di riconciliazione REST a 15s (Livello 2).
3. **Approvazione Esplicita dell'Utente**: Convalida finale manuale prima di qualsiasi chiamata reale in Testnet o produzione.

---

## 3. Bug Noti e Correzioni Architetturali Recenti

* **Dashboard Login & Status Offline Resolution**: Risolto l'inghippo di autenticazione per cui la Dashboard visualizzava *"Login fallito: la password nel build non coincide con ADMIN_PASSWORD nel .env"* e *"System Offline"*. Il problema era causato dall'embedding a tempo di compilazione Vite (`VITE_ADMIN_PASSWORD`) nella build statica JS Nginx rispetto alla sovrascrittura di default a runtime in `_resolve_security_config()`. La correzione ha rimosso la sovrascrittura trasparentemente a runtime ed ha introdotto la resilienza di login lato sia backend che frontend React.
* **Free Available Balance Sizing**: Risolto il bug per cui il risk manager calcolava l'esposizione notionale sulla base del capitale teorico senza considerare i fondi già impegnati su altre posizioni aperte (`max_open_positions: 2`), prevenendo rifiuti per `INSUFFICIENT_FUNDS`.
* **Orphan Order Cleanup**: Aggiunto un ciclo di retry con alert critico su `system:alerts` (`ORPHAN_ORDER_CLEANUP_FAILED`) per garantire che la cancellazione dell'ordine TP/SL opposto non fallisca se l'exchange non risponde.
* **Opposite Order Resizing**: Implementato il ridimensionamento automatico dell'ordine condizionale opposto in caso di esecuzione parziale (`PARTIALLY_FILLED`).
* **Fix `bar_to_ticks()`**: Corretto il calcolo dei tick dinamici all'interno delle candele nei backtest, ancorando il bid/ask al prezzo corrente del tick anziché al close fisso della barra.

---

## 4. Strategie Mantenute vs Rimosse nella Fase A

### 🟢 Strategie MANTENUTE (non rimosse dal codice):
* **`OrderBookImbalanceStrategy`**: **MANTENUTA** (disattivata in `config.yaml`). Non è stata cancellata perché richiede un feed di profondità d'ordine L2/L3 in tempo reale; i test condotti su sole candele 1m non sono quantitativamente attendibili.
* **`MicroStructureBreakoutStrategy`**: **MANTENUTA** (disattivata in `config.yaml`). Non è stata cancellata perché richiede il nastro dei singoli tick L3; le barre 1m non consentono un test accurato della microstruttura.

### 🔴 Strategie RIMOSSE per assenza di edge:
1. **`VWAPDeviationStrategy`**: Rimosso ($PF = 0.42$). Generava finti segnali contrarian nelle fasi di trend prolungato.
2. **`VolatilityExpansionStrategy` (Varianti A e B)**: Rimosso ($PF = 0.31$). Incorreva in un fee drag insostenibile entrando a fine movimento.
3. **`BollingerMeanReversionStrategy`**: Rimosso ($PF = 0.48$). Operava contro l'inerzia dei micro-trend reali.
4. **`llm_optimizer/legacy_apply.py`**: Modulo legacy di riscrittura a runtime del file `config.yaml`, rimosso e sostituito da `advisor.py` (read-only).
