# plans/plan.md — bi (Metabase)

Единственный список задач, сверху вниз по приоритету. Дизайны — `active/`, ресёрч — `research/`,
аудиты — `audits/`. Второго списка не заводить.

> ⛔ **Сквозное условие всех задач: НЕЛЬЗЯ УРОНИТЬ ПРОД-БД `orchestrator` НИ В КАКОМ ВИДЕ.**
> Подключение к ней — только read-only юзер + тайм-ауты, и только в фазе 4. См. `../CLAUDE.md`.

## Контекст (из ресёрча)

- App-БД: PostgreSQL 16.14, контейнер `orchestrator-postgres-1`, база `orchestrator`, **порт наружу
  НЕ проброшен** (loopback `127.0.0.1:5432`) → co-located Metabase входит в сеть `orchestrator_default`
  без экспозиции. Разбор: [`research/metabase-api-server-state-2026-07-27.md`](research/metabase-api-server-state-2026-07-27.md).
- Вариант A (рядом на api) выбран: ~2 ГБ RAM / 1–2 vCPU / ~2 ГБ диск, влезает в свободные ресурсы
  api (6 vCPU, ~14 ГБ свободно). Футпринт/сравнение A vs B:
  [`research/metabase-deployment-options-2026-07-27.md`](research/metabase-deployment-options-2026-07-27.md).

## Фаза 1 — Локальный стек, БЕЗ прод-БД — ✅ закрыта

- [x] `docker-compose.makefile.yaml`: `metabase` (сток `v0.60.7`, `/dev/urandom`,
      `mem_limit 2048M`, `MB_JAVA_OPTS -Xmx1g`, healthcheck `/api/health`, `depends_on bi-postgres healthy`)
      + `bi-postgres` (ОТДЕЛЬНЫЙ, `16.11`) + `nginx` (`1.28-alpine`, basic-auth). Сеть `bi`, volume
      `bi-pgdata`, label-schema. Обвязка Makefile (BUILDKIT/VERSION).
- [x] `nginx/conf.d/metabase.conf` (reverse-proxy → `metabase:3000` + `auth_basic` + `creds.htpasswd`),
      `creds.htpasswd.example`. TLS — по площадке (фаза 2/3, см. research/bi-two-nginx-tls).
- [x] `Makefile` (up/down/destroy/config/logs/ps/htpasswd) + `.env.example` + `.gitignore`.
- [x] **Локальный прогон ПРОЙДЕН 27.07:** без creds→401, с creds→200, `/api/health`→ok, метадата в
      bi-postgres (160 таблиц, лог «PostgreSQL 16.11 application database ✅»), прод-БД не задета.
- [x] `README.md` — quickstart (copy .env.example, make htpasswd, make up).
- [x] Шифр-бэкап метадаты `scripts/db/{db-dump-and-encrypt,db-decrypt-and-restore}.sh` + `make backup/restore` (one-shot postgres-контейнер, AES-256). Проверено 27.07: backup→584K, расшифровка верным ключом→валидный дамп (291 табл.), неверным→мусор.

## Фаза 2 — Раскатка на dev — ✅ HTTP-стек готов (внешний TLS отложен на прод)

Ландшафт api-dev (`91.196.35.191`, зеркало прода): app+postgres(`127.0.0.1:5432`)+rabbitmq +
nginx приложения (`jonasal/nginx-certbot`, host-network, **владеет 80/443**). 6 vCPU / ~14.5 ГБ своб.
DNS `bi-dev.apple-certificate.solutions → 91.196.35.191` готов. Доступ к БД приложения в фазе 2 НЕ трогаем.

**Механизм деплоя — решено: git clone/pull на хосте + compose.** api-dev тянет bi по HTTPS+PAT
(`~/.git-credentials`, credential.helper=store) — проверено `git ls-remote` (SSH-к-GitHub не настроен).

- [x] На api-dev `git clone` в `/opt/bi` (HTTPS+PAT). Docker — через sudo (ops не в docker-группе).
- [x] `.env` (MB_DB_PASS + BI_BACKUP_ENCRYPT_PASSWORD) + `make htpasswd` (dev-креды, скрытый ввод).
- [x] `make up` (профиль http, `nginx` на `127.0.0.1:8088`); UI проверен по SSH-туннелю (basic-auth) 27.07 —
      без creds 401, вход ок, метадата в bi-postgres, прод-БД не задета.
- [ ] Тест источника данных — на DEV-БД (если безопасно), НЕ на прод-БД. (Полноценно — фаза 4.)

⛔ **Внешний TLS на dev — НЕ делаем** (решение владельца 27.07). Причина: **DNS-01 через Spaceship из
РФ невозможен** — RF-хост не дозванивается до API Spaceship (за Cloudflare → 403, замер 27.07); плюс
образ jonasal не знает аутентификатор `spaceship`. Dev доступен только по SSH-туннелю к `:8088`.
Внешний TLS переносится на прод (фаза 3), путь выберем там.

## Фаза 3 — Прод, БЕЗ подключения к прод-БД — ✅ закрыта 27.07.2026

- [x] Стек развёрнут рядом с приложением на `api.apple-certificate.solutions` (`make prod-tls-up`):
      metabase + свой bi-postgres + nginx-tls. Приложению не мешает (mem_limit применены, на хосте ~12 ГБ своб.).
- [x] Порт 3000 наружу НЕ опубликован (проверено `curl`→refused); единственный вход — nginx+basic-auth (root→401).
- [x] **TLS — решение B (DNS-01 Spaceship) СРАБОТАЛО НА ПРОДЕ.** Прежняя запись «отменено» верна только
      для **dev** (api-dev, cf-ray FRA → 403). **Прод (api, cf-ray ARN) до API Spaceship дозванивается** —
      боевой ECDSA-серт `bi.apple-certificate.solutions` выпущен по DNS-01 (staging→прод), автопродление
      jonasal'ом. Фиксы, которых не хватало: `CERTBOT_DNS_AUTHENTICATORS=spaceship`, креды в `spaceship.ini`,
      шаблон vhost вынесен из `user_conf.d`. Разбор dev-провала — `research/bi-two-nginx-tls-2026-07-27.md`.
- [x] **Внешний доступ открыт**: `:8443` наружу (`BI_HTTPS_PORT=8443`), firewall — правило `iptables_rules_bi`
      в **репо vps** `group_vars/api_prd.yaml` (DOCKER-USER RETURN по `--ctorigdstport 8443`, prod-only),
      `make iptables`. Проверено снаружи (2 точки): `/api/health`→200, `/`→401, серт валиден. `MB_SITE_URL`
      с портом `:8443` (иначе Metabase редиректит на `:443` → 403 nginx приложения).

## Фаза 4 — Боевое подключение к app-БД — ✅ закрыта 27.07.2026

> На ПРОД роль создана **вручную** (`docker exec ... psql`, тот же DDL из `create-metabase-user.sh`), а
> НЕ выкатом оркестратора из `main` — в `main` могут быть неотрелиженные изменения приложения, катать его
> на прод нельзя. Пароль сведён с `METABASE_DB_PASSWORD` в `/opt/orchestrator/.env` (совпал по sha) — чтобы
> будущий выкат main (идемпотентный `metabase-db-user`) не перезаписал пароль и не сломал коннект.

- [x] Read-only роль `metabase` в **ПРОД**-БД: НЕ superuser, CONNECT/USAGE/SELECT на `public`,
      `ALTER DEFAULT PRIVILEGES` (будущие таблицы), `statement_timeout=30s`, `idle_in_transaction=60s`.
      **+ hardening по аудиту 27.07:** `CONNECTION LIMIT 20` (не выест пул app-БД, `max_connections=100`)
      и `lock_timeout=15s`. Всё закодифицировано в `orchestrator/db/create-metabase-user.sh`.
- [x] `docker-compose.makefile.prod.yaml`: metabase ∈ `bi` + `orchestrator_default`; источник `postgres:5432/orchestrator`
      подключён в UI под ролью `metabase` — синхронизировано **20 таблиц**, чтение работает.
- [x] Проверено по SSH-туннелю до открытия iptables; аудит подтвердил read-only на живом проде (роль
      писать/DDL/лочить app-БД не может). Откат — `DROP ROLE metabase` + отцепить сеть.
- [x] **Аудиты (чистые агенты) 27.07:** `AUDIT-bi-firewall-8443-2026-07-27.md` (firewall — дефектов нет),
      `AUDIT-bi-prod-deploy-2026-07-27.md` (прод-БД — целостности ничего не угрожает; F1/F2 применены,
      F3 TEMP-via-PUBLIC и F5 default-ACL приняты как низкий риск).

## Открытые вопросы / решения (по мере фаз)

- Версия Metabase — решено: **`v0.60.7`**. Бамп — по мере.
- Где хранить htpasswd/пароли для dev/prod (вне git; менеджер секретов площадки).
- Нужен ли отдельный дашборд-репозиторий/экспорт (serialization) — позже.
