# Panoramica Generale del Sistema (CryptoTraderJules)

## 1. Cos'è il Progetto
CryptoTraderJules è un sistema di trading algoritmico ad alta frequenza/scalping su criptovalute (in particolare `BTC_USDT`), progettato con architettura a microservizi basata su Docker Compose. Il sistema opera in modo nativo su **Crypto.com Spot Exchange**, adottando un modello commissionale asimmetrico (Maker Limit su ingressi e Take Profit, Taker Market su Stop Loss) ed una gestione rigorosa del rischio.

## 2. Architettura Complessiva del Sistema
L'architettura è composta da servizi disaccoppiati ed asincroni che comunicano tramite **Redis Pub/Sub** ed un database temporale **TimescaleDB** per lo storico e la persistenza.

```text
[ Data Ingestion (Crypto.com WS/REST) ] --(ticks:symbol)--> [ Signal Engine ] --(signals:symbol)--> [ Risk Manager ]
                                                                                                          |
                                                                                                 (approved_orders)
                                                                                                          v
[ Crypto.com Exchange API ] <--(REST API)--- [ Order Executor (LiveEngine/PaperEngine) ] <----------------|
             ^                                                     |
             |--------------- (Reconciliation Loop 15s) -----------|
```

### Canali di Comunicazione Inter-Servizio:
* **Market Data**: `data_ingestion` connette il WebSocket di Crypto.com (`wss://stream.crypto.com/exchange/v1/market`), cattura i tick e pubblica sul canale Redis `ticks:{symbol}`.
* **Segnali**: `signal_engine` valuta i tick e le medie mobili, pubblicando i segnali su `signals:{symbol}`.
* **Gestione Rischio**: `risk_manager` valida i segnali, applica il circuit breaker e misura l'esposizione sul *Free Available Balance*, pubblicando su `approved_orders`.
* **Esecuzione**: `order_executor` accoglie gli ordini approvati e gestisce la simulazione Paper o l'invio all'engine Live (attualmente bloccato).

## 3. Stato Attuale del Progetto
* **Paper Trading**: Operativo in modalità asincrona e tracciato su memoria Redis e TimescaleDB.
* **Live Trading Gate**: **BLOCCATO** da un gate di sicurezza esplicito (`NotImplementedError` in `order_executor/live_engine.py` e `order_executor/cryptocom_rest.py`).
* **Motivo del Blocco**: Il gate preserva il conto reale da esecuzioni non sollecitate. Lo sblocco è subordinato alla convalida della redditività statistica della strategia ed al completamento dell'integrazione WebSocket privata (Livello 1).
