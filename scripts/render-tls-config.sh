#!/usr/bin/env bash
# Рендер TLS-конфига для nginx-certbot (prod): креды Spaceship → nginx/spaceship.ini (0600) и
# vhost nginx/bi.conf.template → nginx/user_conf.d/bi.conf (подставляется только $BI_DOMAIN,
# nginx-переменные не трогаются). Оба файла git-ignored. Секреты берутся из .env, домен — дефолт.
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${SPACESHIP_API_KEY:?SPACESHIP_API_KEY не задан в .env}"
: "${SPACESHIP_API_SECRET:?SPACESHIP_API_SECRET не задан в .env}"
BI_DOMAIN="${BI_DOMAIN:-bi.apple-certificate.solutions}"

( umask 077; printf '[spaceship]\napi_key = %s\napi_secret = %s\n' \
    "$SPACESHIP_API_KEY" "$SPACESHIP_API_SECRET" > nginx/spaceship.ini )
mkdir -p nginx/user_conf.d
BI_DOMAIN="$BI_DOMAIN" envsubst '$BI_DOMAIN' < nginx/bi.conf.template > nginx/user_conf.d/bi.conf
echo "rendered: nginx/spaceship.ini (0600) + nginx/user_conf.d/bi.conf ($BI_DOMAIN)"
