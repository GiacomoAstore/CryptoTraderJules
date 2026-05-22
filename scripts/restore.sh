#!/bin/bash
# Script per il ripristino dei volumi di CryptoTrader su una nuova macchina

echo "=> Creazione dei container (senza avviarli) per predisporre i volumi..."
docker compose create

echo "=> Ripristino del volume TimescaleDB..."
docker run --rm -v cryptoscalper_timescaledb_data:/data -v $(pwd):/backup alpine sh -c "cd / && tar xzf /backup/timescaledb_backup.tar.gz"

echo "=> Ripristino del volume Redis..."
docker run --rm -v cryptoscalper_redis_data:/data -v $(pwd):/backup alpine sh -c "cd / && tar xzf /backup/redis_backup.tar.gz"

echo "=> Ripristino dei volumi completato!"
echo ""
echo "Assicurati di aver copiato il file .env e la cartella shared_config/ prima di avviare il sistema."
echo "Per avviare normalmente il sistema, esegui il comando:"
echo "docker compose up -d"
