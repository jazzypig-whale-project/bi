# Metabase на api: состояние сервера и доступ к БД (ресёрч, read-only)

Дата: 2026-07-27. Хост: `api.apple-certificate.solutions` (прод, кластер 1).
Метод: только чтение через `ssh ops@api…` + `sudo -n docker …` / `psql` внутри контейнера.
Изменений НЕ вносилось. Секреты (пароли БД, `SPRING_DATASOURCE_URL` с query-строкой)
НЕ печатались — в отчёте только факт наличия и безопасные поля (host/port/db/user).

## TL;DR (главный вопрос — проброс порта)

БД **НЕ проброшена наружу**. Postgres опубликован только на `127.0.0.1:5432` (loopback хоста),
внешнего доступа нет. Значит: **co-located Metabase — лучший путь**: он входит в docker-сеть
`orchestrator_default` и ходит на `postgres:5432` без какой-либо экспозиции и без изменения
firewall. Отдельная машина потребовала бы публикации порта/туннеля — лишний риск, не нужно.
Ресурсов под co-location в избытке. Существующего Metabase/BI на хосте нет.

## 1. Что за БД (замер)

- Тип/версия: **PostgreSQL 16.14** (образ `postgres:16`), Debian-сборка.
- Контейнер: **`orchestrator-postgres-1`**, статус `Up (healthy)` ~39 ч.
- БД приложения: **`orchestrator`**, размер **~26 MB**, 20 таблиц (кроме `postgres`/шаблонов).
- БД локальная (не внешняя/не managed) — живёт контейнером на этом же хосте.
- Стек приложения (образ / порты):
  - `orchestrator-app-1` (`orchestrator-app`) — `127.0.0.1:8080->8080` (Spring-приложение)
  - `orchestrator-postgres-1` (`postgres:16`) — `127.0.0.1:5432->5432`
  - `orchestrator-rabbitmq-1` (`rabbitmq:3.13-management`) — `0.0.0.0:5671-5672`, `127.0.0.1:15672`
  - `orchestrator-nginx-1` (`jonasal/nginx-certbot`) — сеть `host`
  - Деплой стека — из ДРУГОГО репо (compose `orchestrator`), не из этого Ansible-репо.

## 2. Проброс порта БД (замер — ключевое)

- `docker port orchestrator-postgres-1` → `5432/tcp -> 127.0.0.1:5432` (только loopback).
- `ss -tlnp`: 5432 слушает только `127.0.0.1` (docker-proxy). Наружу (0.0.0.0) — НЕ слушает.
- Наружу опубликованы лишь rabbitmq `5671/5672` на `0.0.0.0`, но их режет firewall (см. §6).
- Вывод: до БД снаружи хоста не достучаться. Внутри docker-сети — по имени `postgres:5432`.

## 3. Docker-сети (замер)

- Сеть приложения+БД: **`orchestrator_default`** (bridge, подсеть 172.19.0.0/16).
  - `orchestrator-postgres-1` — ip 172.19.0.2, алиасы `orchestrator-postgres-1`, **`postgres`**
  - `orchestrator-app-1` — ip 172.19.0.4, алиасы `orchestrator-app-1`, `app`
  - `orchestrator-rabbitmq-1` — ip 172.19.0.3, алиасы `orchestrator-rabbitmq-1`, `rabbitmq`
- Есть также сеть `nginx_default` (nginx при этом реально в `host`).
- **Для Metabase**: подключить его к сети `orchestrator_default`, тогда host БД = `postgres`,
  порт `5432`, база `orchestrator`. Экспозиция не нужна.

## 4. Как приложение подключается к БД (замер, без секретов)

- `SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/orchestrator` (query-строка отброшена).
- `SPRING_DATASOURCE_USERNAME=orchestrator`.
- Пароль: присутствует в env (факт есть), значение НЕ печаталось.
- Т.е. приложение ходит на БД по docker-DNS-имени `postgres` в сети `orchestrator_default`.

## 5. Ресурсы под co-location (замер)

- CPU: **6 vCPU** (`nproc=6`), load average ~0.33 (простаивает).
- RAM: 16 ГБ всего, **~14.3 ГБ available** (used ~1.7 ГБ, остальное buff/cache), swap = 0.
- Текущее потребление стека (`docker stats`): app ~600 MiB, rabbitmq ~178 MiB, postgres ~135 MiB,
  nginx ~14 MiB — суммарно **~0.9 ГБ**.
- Диск: `/` 79 ГБ, занято 30 ГБ (40%), **свободно ~46 ГБ**.
- Вывод: под Metabase (~1–2 ГБ JVM + своя метадата-БД) ресурсов с большим запасом.
  - Замечание (не блокер): rabbitmq в момент замера показал CPU 113% — разовый пик/своя нагрузка,
    к Metabase отношения не имеет; зафиксировано как наблюдение.

## 6. Безопасность (замер + гипотеза)

Подтверждено замером:
- Postgres недоступен снаружи (loopback-only, §2).
- Firewall (`inventories/nl/group_vars/api.yaml`, цепочка `DOCKER-USER`): правило `899` дропает
  весь внешний inbound на published-порты контейнеров (5432/5672/8080/15672 и пр.), исключение —
  5671 (AMQPS) только с `agent_ips`. Т.е. даже опубликуй кто-то 5432 на 0.0.0.0 — его бы срезало.
- Роли БД (`pg_roles`): единственная login-роль — **`orchestrator` (SUPERUSER)**. Остальные —
  штатные встроенные `pg_*` (без логина). **Отдельного read-only пользователя НЕТ.**

Гипотеза / что учесть при внедрении (проверять на месте, не менять сейчас):
- Под Metabase желательно **создать отдельного read-only юзера** в БД `orchestrator`
  (`CONNECT` + `USAGE` + `SELECT` на нужную схему; `default privileges`), а не давать superuser
  `orchestrator`. Это правка в ДРУГОМ репо/на БД — вне этого ресёрча.
- Metabase нужна **своя метадата-БД**: либо встроенный H2 (том), либо отдельная БД `metabase`
  в этом же postgres (тогда `metabase` — отдельный пользователь/БД). Рекомендация — отдельная
  БД в postgres, не H2, для прод-надёжности.
- Web-UI Metabase (порт 3000) наружу публиковать НЕ нужно/нельзя без фильтра: firewall и так
  дропнет, но правильно — проксировать через nginx (как API) либо биндить на loopback + Dozzle-подобный
  доступ. Наружная публикация 3000 = риск, избегать.

## 7. Metabase/BI уже есть? (замер)

Нет: `docker ps -a | grep -i metabase` — пусто, `docker volume ls | grep metabase` — пусто.

## Что осталось «живьём / вне этого ресёрча»

- Точная схема/таблицы БД `orchestrator` (какие данные показывать) — смотреть при постановке дашбордов.
- Создание read-only роли и метадата-БД — операция в стеке `orchestrator` (другой репо), с владельцем.
- Форма деплоя Metabase (в compose `orchestrator` рядом или отдельным compose с `external` сетью
  `orchestrator_default`) — решение по эксплуатации.
