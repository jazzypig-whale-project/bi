#!/usr/bin/env bash
# Проверки перед стартом стека. Аргумент — режим: local | prod.
# Оба режима требуют .env с MB_DB_PASS. prod дополнительно требует nginx/creds.htpasswd (basic-auth
# у nginx-tls) и ключи Spaceship (выпуск серта по DNS-01). Локально nginx нет — creds не нужны.
# Значения не печатает.
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-local}"

test -f .env || { echo "нет .env — скопируй .env.example и заполни секреты"; exit 1; }
# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${MB_DB_PASS:?MB_DB_PASS не задан в .env}"

if [ "$mode" = "prod" ]; then
  test -f nginx/creds.htpasswd || { echo "нет nginx/creds.htpasswd — make htpasswd USER=admin"; exit 1; }
  : "${SPACESHIP_API_KEY:?SPACESHIP_API_KEY не задан в .env (нужен для DNS-01)}"
  : "${SPACESHIP_API_SECRET:?SPACESHIP_API_SECRET не задан в .env}"
fi
echo "preflight ($mode): ok"
