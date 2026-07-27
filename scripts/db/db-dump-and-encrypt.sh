#!/usr/bin/env bash
# Дамп метадата-БД Metabase (bi-postgres) → шифрование AES-256 → файл.
# bash (не sh): нужен pipefail — dash его не держит.
# PG* (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE) — из окружения (Makefile прокидывает из .env).
# ENCRYPT_PASSWORD — отдельный секрет шифрования бэкапа (НЕ пароль БД).
set -euo pipefail

ENCRYPT_PASSWORD=${ENCRYPT_PASSWORD:?ENCRYPT_PASSWORD required}
BACKUP_PATH=${BACKUP_PATH:-/backups/metabase_db_$(date -u +%Y%m%dT%H%M%SZ).dump.enc}

if ! pg_isready >/dev/null; then
    echo "PostgreSQL (bi-postgres) недоступен, отмена." >&2
    exit 1
fi

# --format=custom → pg_restore умеет --clean/--if-exists; query_cache исключаем по ДАННЫМ
# (регенерируется, экономит объём); сжатие 9.
pg_dump \
    --format=custom \
    --no-password \
    --no-comments \
    --compress=9 \
    --exclude-table-data=query_cache \
| openssl enc -aes-256-cbc -pbkdf2 -pass env:ENCRYPT_PASSWORD -out "$BACKUP_PATH"

echo "Бэкап записан: $BACKUP_PATH ($(du -h "$BACKUP_PATH" | cut -f1))"
