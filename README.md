# bi

Аналитика по приложению `orchestrator` через **Metabase**. Self-contained docker-compose стек:
Metabase + отдельный Postgres под его метаданные + (на проде) nginx с TLS и basic-auth. Разворачивается
рядом с приложением на хосте `api.apple-certificate.solutions`; наружу —
`https://bi.apple-certificate.solutions:8443`.

Архитектура и ⛔ железное условие «не уронить прод-БД приложения ни в каком виде» — в
[`CLAUDE.md`](CLAUDE.md); задачи — в [`plans/plan.md`](plans/plan.md).


## Режимы

Стек запускается в одном из двух режимов, режим выбирается переменной `ENV` (по умолчанию `local`).
Разница между режимами — только набор compose-файлов: база `docker-compose.makefile.yaml` + оверрайд
`docker-compose.makefile.<env>.yaml`. Файлы с инфиксом `.makefile.` гоняются ТОЛЬКО через `make`
(docker их не автодетектит — случайный `docker compose up` в каталоге ничего не поднимет).

| `ENV`   | Что поднимается | Вход | app-БД |
|---------|-----------------|------|--------|
| `local` | metabase + bi-postgres | metabase напрямую на `127.0.0.1:${BI_LOCAL_PORT}` (без nginx/basic-auth) | нет |
| `prod`  | + nginx-tls (TLS `:8443`, basic-auth) | `https://<домен>:8443` через basic-auth | read-only роль `metabase` |


## Переменные окружения

Секреты — в `.env` (git-ignored; скопировать из `.env.example`). Всё остальное — дефолты в
`docker-compose.makefile*.yaml`; переопределять там или через окружение, не в `.env`.

### Секреты (`.env`)

| Переменная | Когда нужна | Описание |
|---|---|---|
| `MB_DB_PASS` | всегда | Пароль метадата-БД (`bi-postgres`). Это НЕ пароль app-БД. |
| `BI_BACKUP_ENCRYPT_PASSWORD` | для `backup`/`restore` | Ключ шифрования дампа метадаты (AES-256). |
| `SPACESHIP_API_KEY` / `SPACESHIP_API_SECRET` | для `ENV=prod` | Ключи Spaceship API — выпуск серта по DNS-01. |
| `METABASE_BASE_URL` / `METABASE_BASIC_USERNAME` / `METABASE_BASIC_PASSWORD` / `METABASE_API_KEY` | для `./mbc` | Данные подключения `mbc` к живому инстансу — см. [Metabase as code](#metabase-as-code) ниже. |

### Конфиг (дефолты в compose)

| Переменная | Дефолт | Описание |
|---|---|---|
| `ENV` | `local` | Режим запуска (`local` \| `prod`). |
| `BI_LOCAL_PORT` | `3000` | Loopback-порт metabase в local-режиме. |
| `BI_HTTPS_PORT` | `8443` | Host-порт nginx-tls в prod. |
| `BI_DOMAIN` | `bi.apple-certificate.solutions` | Домен серта (prod, DNS-01). |
| `MB_SITE_URL` | по режиму | URL Metabase. ⚠ На проде ОБЯЗАТЕЛЬНО с `:8443` (без порта Metabase уводит на `:443` → 403 nginx приложения). |
| `STAGING` | `0` | `1` = staging-ACME (тест серта без rate-limit). |
| `METABASE_TAG` / `POSTGRES_TAG` / `NGINX_CERTBOT_BASE_TAG` | пины | Теги образов. |
| `METABASE_MEM_LIMIT` / `METABASE_XMX` | `2048M` / `1g` | Лимит памяти контейнера + heap JVM Metabase. |

Полный список — в `.env.example` и заголовках `docker-compose.makefile*.yaml`.


## make up

Поднимает стек в порядке зависимостей (`bi-postgres` → `metabase` → на проде `nginx-tls`), каждый
сервис с ожиданием healthy. Режим — через `ENV`. Перед стартом `preflight` проверяет, что нужные
переменные/файлы на месте (для prod — креды Spaceship и `nginx/creds.htpasswd`).

```bash
make up                 # local: metabase на 127.0.0.1:3000, без nginx
make up ENV=prod        # prod: nginx-tls :8443 + app-БД (рендерит серт-конфиг перед стартом)
```

Слои можно поднимать по отдельности: `make up-bi-postgres`, `make up-metabase`,
`make up-nginx-tls` (каждый тянет предыдущие).


## make build

Собирает образ `nginx-tls` (nginx-certbot + плагин Spaceship DNS-01) — отдельно от запуска. Metabase
и Postgres — стоковые образы, не собираются.

```bash
make build
```


## make htpasswd

Добавляет/меняет пользователя basic-auth для prod-nginx (`nginx/creds.htpasswd`). Пароль спрашивается
СКРЫТО (не попадает в history/ps/argv), остальные пользователи сохраняются. nginx перечитывает файл на
каждый запрос — reload не нужен.

```bash
make htpasswd USER=admin
```


## make backup / make restore

Шифрованный (AES-256) дамп и восстановление метадата-БД Metabase (`bi-postgres`). Нужен
`BI_BACKUP_ENCRYPT_PASSWORD` в `.env`.

```bash
make backup                                    # -> backups/metabase_db_<ts>.dump.enc
make restore FILE=metabase_db_<ts>.dump.enc    # ⚠ РАЗРУШАЮЩЕЕ: затирает текущую метадату
```


## Прочее

| Цель | Описание |
|---|---|
| `make config` | Рендер финального compose (`ENV=local` \| `prod`). |
| `make down` | Остановить и удалить контейнеры (том метадаты сохраняется). |
| `make destroy` | ⚠ РАЗРУШАЮЩЕЕ: `down` + удаление тома `bi-pgdata` — данные Metabase теряются. |

Подключение источника (app-БД `orchestrator` под read-only ролью `metabase`) настраивается в UI —
[`docs/connect-datasource.md`](docs/connect-datasource.md). ⛔ app-БД — только read-only.

`.env`, `nginx/creds.htpasswd`, `nginx/spaceship.ini`, `backups/`, `pgdata/` — в `.gitignore`,
в git не коммитятся.


## Metabase as code

Дашборды и вопросы на инстансе управляются как код: YAML под `collections/`, `cards/` и
`dashboards/` — источник истины, инструмент `./mbc` синхронизирует их с живым Metabase (v0.60.7 OSS)
через HTTP API напрямую (Basic auth + API key, за nginx).

```bash
./mbc validate   # офлайн-проверка YAML, exit 0/1
./mbc diff       # сравнение с живым инстансом, exit 0=нет изменений / 2=дрейф / 1=ошибка
./mbc apply --yes   # применить файлы к инстансу
```

Или через Makefile: `make mbc-validate`, `make mbc-diff`, `make mbc-test`.

Подробности — команды, гарантии безопасности, переименование логических ключей, известные
ограничения — в [`docs/metabase-as-code.md`](docs/metabase-as-code.md).
