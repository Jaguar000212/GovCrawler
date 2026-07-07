#!/usr/bin/env bash
# Daily Postgres backup — pg_dump via the running `db` container, timestamped,
# 14-day retention. Run from deploy/ (or set COMPOSE_FILE).
#
# Usage: ./backup.sh
# Cron:  0 3 * * * cd /path/to/GovCrawler/deploy && ./backup.sh >> backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

BACKUP_DIR="./backups"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUT_FILE="$BACKUP_DIR/govcrawler_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Backing up to $OUT_FILE ..."
docker compose exec -T db pg_dump -U govcrawler govcrawler | gzip > "$OUT_FILE"

echo "Pruning backups older than ${RETENTION_DAYS}d ..."
find "$BACKUP_DIR" -name 'govcrawler_*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete

echo "Done: $(du -h "$OUT_FILE" | cut -f1) — $(ls "$BACKUP_DIR"/govcrawler_*.sql.gz | wc -l) backup(s) retained."
