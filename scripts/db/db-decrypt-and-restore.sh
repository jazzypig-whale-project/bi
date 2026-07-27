#!/usr/bin/env bash
# Расшифровка бэкапа → восстановление метадата-БД Metabase (bi-postgres). ⚠ РАЗРУШАЮЩЕЕ:
# pg_restore --clean --if-exists пересоздаёт объекты (текущая метадата затирается).
# bash (не sh): нужен pipefail.
# ⚠ Останови Metabase перед restore (иначе он держит соединения/схему): docker compose stop metabase.
set -euo pipefail

ENCRYPT_PASSWORD=${ENCRYPT_PASSWORD:?ENCRYPT_PASSWORD required}
BACKUP_PATH=${BACKUP_PATH:?BACKUP_PATH required (путь к .dump.enc)}
PGDATABASE=${PGDATABASE:-metabase}

if ! pg_isready >/dev/null; then
    echo "PostgreSQL (bi-postgres) недоступен, отмена." >&2
    exit 1
fi
if [ ! -r "$BACKUP_PATH" ]; then
    echo "Файл бэкапа не найден/не читается: $BACKUP_PATH" >&2
    exit 1
fi

openssl enc -d -aes-256-cbc -pbkdf2 -pass env:ENCRYPT_PASSWORD -in "$BACKUP_PATH" \
| pg_restore --clean --if-exists --no-owner --dbname="$PGDATABASE"

echo "Восстановлено из: $BACKUP_PATH → БД $PGDATABASE"
