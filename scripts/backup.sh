#!/bin/bash
# Script per il backup dei volumi di CryptoTrader

echo "=> Esportazione del volume TimescaleDB..."
docker run --rm -v cryptoscalper_timescaledb_data:/data -v $(pwd):/backup alpine tar czf /backup/timescaledb_backup.tar.gz -C / data

echo "=> Esportazione del volume Redis..."
docker run --rm -v cryptoscalper_redis_data:/data -v $(pwd):/backup alpine tar czf /backup/redis_backup.tar.gz -C / data

echo "=> Backup completato!"
echo "Troverai i file timescaledb_backup.tar.gz e redis_backup.tar.gz nella directory corrente."
echo ""
echo "!!! IMPORTANTE !!!"
echo "Ricorda di copiare a mano anche i seguenti file sulla nuova macchina:"
echo "- Il file .env con le credenziali"
echo "- La cartella shared_config/ con il config.yaml"
echo "- Eventuale docker-compose.override.yml"
