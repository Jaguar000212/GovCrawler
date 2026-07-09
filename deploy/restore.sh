#!/usr/bin/env bash
# Restore a backup.sh dump into the running `db` container. Drops and
# recreates the target database first — destructive, confirms before acting.
#
# Usage: ./restore.sh backups/govcrawler_20260101_030000.sql.gz
set -euo pipefail
cd "$(dirname "$0")"

DUMP_FILE="${1:-}"
if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
    echo "Usage: ./restore.sh <path-to-dump.sql.gz>" >&2
    exit 1
fi

echo "This will DROP and recreate the 'govcrawler' database, replacing all current data"
echo "with the contents of: $DUMP_FILE"
read -rp "Type 'restore' to continue: " confirm
if [ "$confirm" != "restore" ]; then
    echo "Aborted."
    exit 1
fi

echo "Stopping api/dispatcher (so nothing writes during restore) ..."
docker compose stop api dispatcher

echo "Dropping and recreating govcrawler ..."
docker compose exec -T db psql -U govcrawler -d postgres -c "DROP DATABASE IF EXISTS govcrawler;"
docker compose exec -T db psql -U govcrawler -d postgres -c "CREATE DATABASE govcrawler OWNER govcrawler;"

echo "Restoring $DUMP_FILE ..."
gunzip -c "$DUMP_FILE" | docker compose exec -T db psql -U govcrawler -d govcrawler

echo "Restarting api/dispatcher ..."
docker compose up -d api dispatcher

echo "Done. Verify with: docker compose exec db psql -U govcrawler -d govcrawler -c 'SELECT count(*) FROM leads;'"
