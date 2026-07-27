#!/usr/bin/env bash
# Добавить/сменить basic-auth юзера в nginx/creds.htpasswd. Пароль — скрытым вводом
# (не попадает в history/ps/argv). Остальных юзеров сохраняет. Пишет IN-PLACE (тот же inode) —
# bind-mount в nginx видит изменение, reload не нужен (nginx читает htpasswd на каждый запрос).
set -euo pipefail
cd "$(dirname "$0")/.."
user="${1:-}"
test -n "$user" || { echo "USER обязателен: make htpasswd USER=<имя>"; exit 1; }

printf 'Пароль для %s (ввод скрыт): ' "$user"
stty -echo 2>/dev/null || true
trap 'stty echo 2>/dev/null || true' EXIT INT TERM
read -r pw
stty echo 2>/dev/null || true
trap - EXIT INT TERM
echo
test -n "$pw" || { echo "пустой пароль — отмена"; exit 1; }

hash="$(printf '%s' "$pw" | openssl passwd -apr1 -stdin)"
touch nginx/creds.htpasswd
{ grep -v "^$user:" nginx/creds.htpasswd 2>/dev/null || true; printf '%s:%s\n' "$user" "$hash"; } \
  > nginx/creds.htpasswd.tmp
cat nginx/creds.htpasswd.tmp > nginx/creds.htpasswd
rm -f nginx/creds.htpasswd.tmp
echo "nginx/creds.htpasswd: '$user' добавлен/обновлён (остальные сохранены; пароль не в history/ps/argv)"
