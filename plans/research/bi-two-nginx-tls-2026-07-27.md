# Ресёрч: два nginx на api-хосте, 443 для BI, сертификат bi.apple-certificate.solutions

Дата: 2026-07-27. Статус: **только исследование, ничего не менялось**. Прод (`api`) не трогали,
приложение не рестартовали. Замеры — read-only (ssh `ops@api`, `sudo -n docker …`, `ss`, `nginx -T`,
`dig`). Секреты/приватные ключи не печатались.

Соседний инфра-репо (Ansible-IaC). Раскатка BI — отдельно, с разрешения владельца.

---

## 1. Как устроен nginx приложения (замерено)

- Контейнер **`orchestrator-nginx-1`**, образ **`jonasal/nginx-certbot:6.2.0-alpine`** —
  тот же образ, что и роль `nginx_certbot` в соседнем инфра-репо.
- **Сеть: `network_mode: host`**. Compose приложения — в `/opt/orchestrator` (отдельный проект,
  вне обоих наших репозиториев; управляется командой приложения).
- **Слушает `0.0.0.0:80` и `0.0.0.0:443` (+ `[::]`), оба как `default_server reuseport`** — то есть
  **владеет 80 и 443 на хосте эксклюзивно** (7 рабочих процессов, замер `ss -tlnp`).
- vhost (`/etc/nginx/conf.d/api.conf`): `server_name api.apple-certificate.solutions`,
  `listen 443 ssl default_server`, `proxy_pass http://127.0.0.1:8080` (контейнер `orchestrator-app-1`,
  опубликован loopback-only `127.0.0.1:8080`).
- **L7 access-control**: `/etc/nginx/allow.d/api.apple-certificate.solutions.conf` — список `allow`
  (агенты/партнёр `194.54.14.0/24`/пул npv01) + `deny all`. То есть на L7 чужой IP получает 403.
- Порт 80 (`redirector.conf`): отдаёт `/.well-known/acme-challenge` из `/var/www/letsencrypt`,
  всё остальное — `301 → https`.

**Хост.** Единственный публичный адрес — **`38.244.138.58/32` на `eth0`** (второго внешнего IP нет;
остальное — docker-мосты `172.17/18/19`). 6 vCPU / 16 ГБ.

## 2. Как приложение получает TLS-сертификат (замерено)

- Механизм — встроенный в образ **certbot + Let's Encrypt по HTTP-01** (webroot `/var/www/letsencrypt`).
  Live-lineage один: **`/etc/letsencrypt/live/api.apple-certificate.solutions/`**, в
  named-volume `letsencrypt` проекта `/opt/orchestrator` (НЕ `/opt/nginx`).
- Файрвол api (`vps` роль iptables, `group_vars/api.yaml`): **`:80` открыт всему миру** (ACME http-01),
  **`:443` ограничен на L3** источниками `agent_ips + agent_ips_web + partner_ips + resend_ips + dev_ips`.
  То есть 443 **не** открыт миру — это важно для BI (см. §4).
- Cloudflare-origin / вручную положенный серт — **нет**. Чистый certbot HTTP-01.

## 3. DNS

- **`bi.apple-certificate.solutions` НЕ резолвится** (записи нет) — `dig +short` пусто.
- `api.apple-certificate.solutions` → `38.244.138.58`. Apex `A` пуст.
- Зона `apple-certificate.solutions` — на **Spaceship** (`NS launch1/launch2.spaceship.net`).
- В соседнем инфра-репо **уже есть управление зоной через API Spaceship**: роль `nginx_certbot`
  умеет DNS-01 через плагин `certbot-dns-spaceship` (`nginx_certbot_challenge: dns`), а ключ лежит в
  vault (`vault_spaceship_api_key/secret`, скоуп `dnsrecords:read+write` — правит записи, домен увести
  не может). То есть создать `A`-запись `bi → 38.244.138.58` и выпустить DNS-01-серт — **инфраструктура
  готова**, ключ есть.

## 4. Разруливание 443 — сравнение A–D

Жёсткое ограничение: **два nginx не могут оба слушать 443**, а app-nginx владеет 443 эксклюзивно
(host-network, `default_server`). Плюс железное правило — **минимум риска для прод-приложения**.

### (A) App-nginx фронтит BI (добавить vhost `server_name bi.…` в orchestrator-nginx)
Bi-nginx уходит на loopback-порт без TLS (напр. `127.0.0.1:8088`), app-nginx проксирует на него.
- **Плюсы**: один терминатор TLS, **штатный 443** (без порта в URL), сертификат BI выпускается
  автоматически существующим certbot по **HTTP-01** (порт 80 уже открыт миру) — отдельный ACME не нужен.
- **Минусы / риск**:
  - **правка ПРОД-nginx приложения** в `/opt/orchestrator` (вне наших репо) — только с командой
    приложения; кривой reload задевает боевой vhost. Митигируется: отдельный файл
    `user_conf.d/bi.*.conf` (аддитивный SNI-vhost), `nginx -t` до reload.
  - **BI ходит с произвольных IP**, а `:443` на L3 закрыт allow-листом. Чтобы пустить BI-юзеров,
    **:443 надо открыть миру на L3** (или на список BI-IP, которых нет). Приложение при этом остаётся
    прикрыто своим L7 `deny all`+allow-лист, но **defense-in-depth на L3 снимается** — это изменение
    security-постуры прода. Решение владельца.

### (B) Свой bi-nginx на втором host-порту (напр. `8443`) со СВОИМ сертом
- **Плюсы**: **ноль правок `/opt/orchestrator`** — полная изоляция от прод-nginx, **риск для приложения
  ~нулевой**. BI-стек self-contained (свой `jonasal/nginx-certbot`).
- **Минусы**:
  - **порт 80 занят приложением → HTTP-01 для BI невозможен**. Значит серт только по **DNS-01
    (Spaceship)** — но тулинг и ключ уже есть в инфра-репо (см. §3), это решаемо.
  - **нестандартный порт `:8443`** в URL (`https://bi.…:8443`) — некрасиво, часть корпоративных
    сетей режет. Открыть `8443` в файрволе api (L3 миру, т.к. IP произвольные; фильтр — basic-auth).

### (C) bi-nginx на 443 на ВЫДЕЛЕННОМ IP
- **Недоступно сейчас**: у хоста один публичный адрес (`38.244.138.58/32`). Нужен заказ второго IP у
  провайдера. Отклонено (до заказа IP).

### (D) Внешний общий reverse-proxy / Cloudflare терминирует TLS, оба апстрима по http
- Зона на Spaceship, api **не** проксируется через CF; чтобы так сделать — переносить ingress прода в
  CF (проксирование api). **Трогает боевой путь приложения** и добавляет внешнюю зависимость.
  Для задачи BI — избыточно и рискованно. Отклонено.

### Рекомендация
Приоритет железного правила — **минимум риска для прод-приложения**, поэтому по умолчанию:

> **(B) — свой bi-nginx, отдельный порт, DNS-01 Spaceship. Ноль изменений в `/opt/orchestrator`.**
> Это единственный вариант, при котором прод-nginx приложения не трогается вообще.

Если владелец решит, что **штатный 443 для BI обязателен** (нет `:8443` в URL) — тогда **(A)**, но
осознанно: правка прод-nginx с командой приложения + открытие `:443` миру на L3 (приложение остаётся за
своим L7 `deny all`). При (A) сертификат BI не требует отдельного ACME — его выпустит существующий
certbot по HTTP-01.

Компромисс-нюанс: (B) даёт изоляцию ценой нестандартного порта; (A) даёт штатный 443 ценой двух правок
прода. «Меньше всего трогает прод» = **B**.

## 5. Конкретный путь по сертификату `bi.apple-certificate.solutions`

**Предусловие для ЛЮБОГО варианта:** создать DNS-запись **`A bi.apple-certificate.solutions →
38.244.138.58`** в зоне Spaceship (владелец зоны / тем же API-ключом, что уже в vault).

- **Вариант B (рекомендуемый) — DNS-01, свой certbot в bi-стеке:**
  порт 80 занят приложением, поэтому HTTP-01 недоступен → **только DNS-01**. Готовый образец —
  роль `nginx_certbot` (соседний инфра-репо) в режиме `challenge: dns`: derived-образ
  `jonasal/nginx-certbot` + плагин `certbot-dns-spaceship==1.0.4` (Debian-база, не alpine —
  musl ломает pip-сборку), `dns-credentials.ini` (0600, из vault `vault_spaceship_api_key/secret`),
  `CERTBOT_DNS_PROPAGATION_SECONDS: 60`. Перенести этот механизм в `bi/docker-compose` (или отдельный
  override), заменив текущий `nginx` BI (сейчас `nginx:1.28-alpine`, `8080:80`, только HTTP+basic-auth)
  на `nginx-certbot` с TLS-vhost на `8443` и тем же basic-auth. **Обязательно сначала
  `STAGING=1`/`--dry-run`**, затем прод-серт.

- **Вариант A — переиспользовать certbot приложения (HTTP-01), отдельный certbot НЕ нужен:**
  добавить в orchestrator-nginx `user_conf.d/bi.apple-certificate.solutions.conf` с
  `server_name bi.…` + basic-auth + `proxy_pass` на bi-nginx (loopback, без TLS). Образ сам выпустит
  вторую lineage по HTTP-01 (80 уже открыт миру). BI-nginx тогда — без certbot, просто basic-auth на
  внутреннем порту.

- **Cloudflare-origin** — неприменимо (зона на Spaceship, api не за CF). Не рекомендуется.

## Открытые вопросы владельцу
1. Штатный `:443` для BI обязателен (→ A, правка прод-nginx + открыть 443 миру на L3) или допустим
   отдельный порт `:8443` (→ B, изоляция, DNS-01)?
2. Кто правит зону Spaceship и можно ли переиспользовать API-ключ из `vps`-vault для записи `bi A`?
3. При (A) — согласование с командой приложения на правку `/opt/orchestrator` nginx.
