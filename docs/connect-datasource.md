# Подключение БД приложения (orchestrator) к Metabase

Как настроить источник данных: Metabase читает БД `orchestrator` под **read-only** ролью `metabase`.
⛔ app-БД трогаем только read-only ролью (см. [`../CLAUDE.md`](../CLAUDE.md), не уронить прод-БД).

## Предпосылки

1. **Read-only роль `metabase`** в БД orchestrator. На проде создана ВРУЧНУЮ (тот же DDL, что в
   сервисе `metabase-db-user` репо orchestrator — `CONNECT`/`USAGE`/`SELECT`, не superuser, таймауты +
   `CONNECTION LIMIT`), НЕ выкатом orchestrator из `main`. Пароль — в `/opt/orchestrator/.env`
   `METABASE_DB_PASSWORD` (сведён с тем, что создан руками).
2. **Сеть:** в prod-режиме контейнер `metabase` подключён к внешней сети приложения
   `orchestrator_default` (через `docker-compose.makefile.prod.yaml`) → достаёт app-БД по хосту
   `postgres:5432`. Постоянно, не ad-hoc.

## Поднять стек на проде (co-located, с сетью приложения)

На хосте приложения (api), из `/opt/bi`:
```sh
make up ENV=prod      # nginx-tls :8443 + metabase в сети приложения
make down             # остановить (том метадаты сохраняется)
```

## Взять пароль роли (на хосте приложения)
```sh
sudo grep '^METABASE_DB_PASSWORD=' /opt/orchestrator/.env | cut -d= -f2
```

## Открыть Metabase
Снаружи (после открытия firewall): **https://bi.apple-certificate.solutions:8443** → basic-auth →
войти админом Metabase. Или по SSH-туннелю на loopback (до открытия доступа):
```sh
ssh -N -L 8443:127.0.0.1:8443 ops@api.apple-certificate.solutions
# браузер: https://localhost:8443/ (серт на домен → варнинг на localhost, принять) → basic-auth
```

## Добавить источник в UI
⚙ → **Admin settings** → **Databases** → **Add database**:

| Поле | Значение |
|---|---|
| Database type | **PostgreSQL** |
| Display name | `Orchestrator (prod, read-only)` |
| Host | **`postgres`** |
| Port | **`5432`** |
| Database name | **`orchestrator`** |
| Username | **`metabase`** |
| Password | *(из шага выше)* |
| Use SSL | **выкл** (внутренняя docker-сеть) |

**Save** → Metabase проверит коннект и синхронизирует схему → таблицы доступны на чтение.

## Нюансы
- Роль **read-only** (SELECT). «Actions»/writeback Metabase не работают — для аналитики норма.
- `statement_timeout=30s` / `idle_in_transaction_session_timeout=60s` / `lock_timeout=15s` +
  `CONNECTION LIMIT 20` — тяжёлый/зависший запрос сам оборвётся, пул app-БД не выест (защита app-БД).
- Хост `postgres` = app-БД (сеть `orchestrator_default`). Метадата самого Metabase — отдельно в
  `bi-postgres` (сеть `bi`), не путать.
- ⚠ `MB_SITE_URL` на проде — с `:8443` (без порта Metabase уводит на `:443` → 403 nginx приложения).
