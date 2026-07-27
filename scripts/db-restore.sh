#!/usr/bin/env bash
# ⚠ РАЗРУШАЮЩЕЕ: restore метадаты из backups/<FILE>. Останавливает metabase на время restore,
# затем запускает обратно. One-shot postgres-контейнер на сети bi (scripts/db/db-decrypt-and-restore.sh).
# Секреты в argv НЕ уходят (`-e ИМЯ` без значения); BACKUP_PATH — имя файла, не секрет.
set -euo pipefail
cd "$(dirname "$0")/.."
file="${1:-}"
test -n "$file" || { echo "FILE обязателен: make restore FILE=<имя в backups/>"; exit 1; }
backup_dir="${BACKUP_DIR:-backups}"

# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${MB_DB_PASS:?MB_DB_PASS не задан в .env}"
: "${BI_BACKUP_ENCRYPT_PASSWORD:?BI_BACKUP_ENCRYPT_PASSWORD не задан в .env}"
export PGPASSWORD="$MB_DB_PASS"
export ENCRYPT_PASSWORD="$BI_BACKUP_ENCRYPT_PASSWORD"

echo "⚠ Затрёт текущую метадату. Останавливаю metabase на время restore."
docker compose -f docker-compose.makefile.yaml stop metabase
docker run --rm --network bi \
  -v "$PWD/scripts/db:/scripts:ro" -v "$PWD/$backup_dir:/backups:ro" \
  -e PGHOST=bi-postgres -e PGPORT=5432 \
  -e PGUSER="${MB_DB_USER:-metabase}" -e PGDATABASE="${MB_DB_DBNAME:-metabase}" \
  -e PGPASSWORD -e ENCRYPT_PASSWORD -e BACKUP_PATH="/backups/$file" \
  "postgres:${POSTGRES_TAG:-16.11}" bash /scripts/db-decrypt-and-restore.sh
docker compose -f docker-compose.makefile.yaml start metabase
