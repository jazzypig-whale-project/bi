#!/usr/bin/env bash
# Шифр-бэкап метадата-БД (bi-postgres) → backups/. One-shot postgres-контейнер на сети bi:
# дамп + AES-256 (scripts/db/db-dump-and-encrypt.sh внутри контейнера). Секреты в argv НЕ уходят —
# передаются `-e ИМЯ` без значения (docker берёт из окружения). Имя БД/юзер/тег — дефолты (как в compose).
set -euo pipefail
cd "$(dirname "$0")/.."
backup_dir="${BACKUP_DIR:-backups}"
mkdir -p "$backup_dir"

# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${MB_DB_PASS:?MB_DB_PASS не задан в .env}"
: "${BI_BACKUP_ENCRYPT_PASSWORD:?BI_BACKUP_ENCRYPT_PASSWORD не задан в .env}"
export PGPASSWORD="$MB_DB_PASS"
export ENCRYPT_PASSWORD="$BI_BACKUP_ENCRYPT_PASSWORD"

docker run --rm --network bi \
  -v "$PWD/scripts/db:/scripts:ro" -v "$PWD/$backup_dir:/backups" \
  -e PGHOST=bi-postgres -e PGPORT=5432 \
  -e PGUSER="${MB_DB_USER:-metabase}" -e PGDATABASE="${MB_DB_DBNAME:-metabase}" \
  -e PGPASSWORD -e ENCRYPT_PASSWORD \
  "postgres:${POSTGRES_TAG:-16.11}" bash /scripts/db-dump-and-encrypt.sh
