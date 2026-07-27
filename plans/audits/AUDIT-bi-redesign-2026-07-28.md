# AUDIT — редизайн BI-стека (compose-split + Makefile + scripts)

> **НЕЗАВИСИМЫЙ ВНЕШНИЙ ОТЧЁТ. СНИМОК на 2026-07-28. НЕ РЕДАКТИРУЕТСЯ.**
> Аудитор — чистый агент, не участвовал в изменении. Область: незакоммиченный
> редизайн `/home/seranton/bi` (compose-split base/local/prod, переписанный Makefile,
> 5 скриптов в `scripts/`, сокращённый `.env`). Прод сейчас крутит СТАРУЮ структуру.
> Проверка — только чтение + клиентские `make config` / `make -n` / `docker compose config`.
> Прод по docker не опрашивался (нет контекста/SSH с раннера) — см. «Непроверенное».

## Вердикт

Механика редизойна **корректна и безопасна для прод-данных**. Оба режима валидны
(`make config ENV=local|prod` рендерятся без ошибок), prod сохраняет у `metabase` ОБЕ
сети (`bi` + `orchestrator_default`), local публикует metabase только на `127.0.0.1`,
`profiles` вычищены, имена (project `bi`; сервисы `bi-postgres`/`metabase`/`nginx-tls`;
сети `bi`/`orchestrator_default`; тома `bi-pgdata`/`bi-letsencrypt`) **идентичны старой
структуре** → миграция = recreate-in-place, `down -v` нигде не в цепочке `up`.
Багов уровня «ломает прод / теряет данные при обычном событии» НЕ найдено.
Находки — один миграционный гейт по `.env` и доковый дрейф.

---

## Что проверено и чем

| Вопрос | Как | Итог |
|---|---|---|
| Слияние local/prod валидно | `make config ENV=local`, `ENV=prod` | OK, оба рендерятся |
| prod metabase несёт обе сети | config ENV=prod → `networks: bi, orchestrator_default` | OK, `bi` не теряется |
| local только loopback | config → `host_ip: 127.0.0.1`, `published: 3000` | OK |
| `profiles` вычищены | grep по новым compose + вывод config | OK, ни одного |
| Имена = проду (recreate-in-place) | сверка с `git show HEAD:docker-compose*.yml` | OK, совпадают |
| `ENV` доходит до под-make | `make -n up ENV=prod` | OK, под-make берёт prod-файлы (`export ENV` + CLI-override) |
| local `up` стоп на metabase | `make -n up` | OK, nginx не поднимается |
| порядок prod | `make -n up ENV=prod` | OK: bi-postgres → metabase → nginx-tls |
| `build` из нужных файлов | `make -n build` | OK, base+prod, `build nginx-tls` |
| `down`/`destroy` | `make -n down`, `make -n destroy` | `down`=base+local+prod (том жив); `destroy`=`base down -v` (том сносит) |
| только `MB_DB_PASS` обязателен | grep `:\?` по новым compose | OK, единственный `:?`; остальное `:-` дефолты |
| prod-дефолты не замаскированы | `docker compose --env-file <clean> … config` | MB_SITE_URL → `https://bi.apple-certificate.solutions:8443/`, port 8443 |
| секреты не в git | `git ls-files`, `.gitignore` | OK (см. ниже) |

---

## Находки по severity

### [Тихая деградация / гейт миграции] Легаси-`.env` на проде может перебить дефолты
Редизайн перенёс не-секреты (`MB_SITE_URL`, теги образов, порты, имена БД) в дефолты
compose (`${VAR:-…}`), а `.env.example` сократил до 4 секретов. Но `.env` — per-machine и
git-ignored: на ДЕВ-машине он ещё содержит легаси-ключи (`MB_SITE_URL`, `METABASE_TAG`,
`POSTGRES_TAG`, `NGINX_TAG`, `BI_HTTP_PORT`, `MB_DB_DBNAME`, `MB_DB_USER`). Из-за этого
`make config ENV=prod` на деве показал `MB_SITE_URL: http://localhost:8088/` (значение
из дев-`.env`), а НЕ прод-дефолт `:8443`. С чистым env-файлом дефолт `:8443` подтверждён.
- **Сценарий отказа (при миграции прода):** если прод-`.env` сохранит легаси-`MB_SITE_URL`
  без `:8443` (или иной устаревший), он тихо победит правильный дефолт → Metabase уводит
  на `:443` → 403 nginx приложения. Данные НЕ теряются, но UI ломается.
- **Действие оператору перед `make up ENV=prod`:** привести прод-`.env` к новому
  `.env.example` (только секреты) ЛИБО убедиться, что оставшийся `MB_SITE_URL` несёт `:8443`.
- Воспроизведение дефолта: `docker compose --env-file <(printf 'MB_DB_PASS=x\nSPACESHIP_API_KEY=x\nSPACESHIP_API_SECRET=x\n') -f docker-compose.makefile.yaml -f docker-compose.makefile.prod.yaml config | grep MB_SITE_URL`
- **Требует проверки на проде:** содержимое живого `/home/…/bi/.env` (аудитор не читал прод).

### [Тихая деградация — hardening] Секреты бэкапа/restore уходят в argv `docker run`
`scripts/db-backup.sh:16` и `scripts/db-restore.sh:20` передают значения через
`-e PGPASSWORD="$MB_DB_PASS"`, `-e ENCRYPT_PASSWORD="$BI_BACKUP_ENCRYPT_PASSWORD"` — значение
попадает в argv процесса `docker` → видно в `ps aux` / `/proc/<pid>/cmdline` любому юзеру хоста
на время прогона. Контраст: `scripts/htpasswd-add.sh` сделан правильно (пароль по stdin в
`openssl passwd -apr1 -stdin`, не в argv).
- **Сценарий:** локальный непривилегированный юзер на хосте снимает секрет в окно бэкапа.
  Severity низкая (хост доверенный, окно секунды), но это регресс относительно htpasswd-паттерна.
- **Фикс:** `set -a; . ./.env` уже экспортит переменные → передавать `-e PGPASSWORD` (без `=value`),
  docker подхватит из окружения, argv чист. То же для `ENCRYPT_PASSWORD`, `PGDATABASE` и т.п.

### [Доковый дрейф] `CLAUDE.md`/`docs/connect-datasource.md` зовут удалённый `make prod-up`
Целей `prod-up`/`prod-tls-up`/`tls-up` в новом Makefile НЕТ (единственный вход — `make up ENV=prod`).
Но `CLAUDE.md:36,53,71` и `docs/connect-datasource.md:19,22` всё ещё пишут `make prod-up` и
`docker-compose.prod.yml … --profile http`. `CLAUDE.md` в этом редизайне правился (157 строк diff) —
значит это пропуск, а не «ещё не дошли».
- **Сценарий:** оператор в момент миграции идёт по `CLAUDE.md`, набирает `make prod-up` →
  `No rule to make target`. Не данные, но трение ровно в чувствительный момент.
- **Фикс:** заменить на `make up ENV=prod` в `CLAUDE.md` и `docs/connect-datasource.md`.
  (`plans/plan.md` — исторические `[x]` про старые имена файлов; это летопись, править не обязательно.)

### [Инфо — как задумано, риска нет]
- **Единственный путь потери данных — `make destroy`** (`docker compose … down -v`, том `bi-pgdata`).
  Отдельная явная цель, ни из чего не вызывается, помечена `⚠ РАЗРУШАЮЩЕЕ`. `down` (base+local+prod)
  сносит контейнеры обоих режимов, но том сохраняет (`--remove-orphans`, без `-v`). Обе корректны.
- **restore** останавливает/запускает metabase через ПЕРЕИМЕНОВАННЫЙ base-файл
  (`docker-compose.makefile.yaml`, `db-restore.sh:24,32`) — стейл-ссылки на старый `docker-compose.yml`
  нет; `stop`/`start` не пересоздают контейнер → прод-сеть `orchestrator_default` у metabase уцелеет.
- **Скрипты:** `preflight.sh` — prod требует `creds.htpasswd`+ключи Spaceship, local только `MB_DB_PASS` (OK);
  `render-tls-config.sh` — `spaceship.ini` под `umask 077`, `envsubst '$BI_DOMAIN'` не трогает
  nginx-`$`-переменные (OK); `htpasswd-add.sh` — скрытый ввод, in-place через truncate
  (`cat tmp > file`, inode жив → bind-mount видит), прочие юзеры сохранены (OK).
- **Порт 3000 на проде не публикуется** (prod-оверрайд `ports` не задаёт; наружу только nginx-tls `:8443`).
- **Секреты не в git:** `git ls-files` возвращает только `*.example` (`.env.example`,
  `nginx/creds.htpasswd.example`, `nginx/spaceship.ini.example`); реальные `.env`,
  `nginx/creds.htpasswd`, `nginx/spaceship.ini` покрыты `.gitignore`. Значения не читались/не приводятся.
- **Дев-`.env`-косметика:** локальный `MB_SITE_URL=…:8088` при порте `3000` — рассинхрон только
  дев-`.env`, в поставку (`.env.example`) не входит, прод не затрагивает.

---

## Непроверенное (нужен доступ к проду)
1. **Живые имена контейнеров/томов на проде** (`docker ps`, `docker volume ls` на хосте `api`).
   Вывод «recreate-in-place» сделан по идентичности имён в удалённых `docker-compose*.yml`
   (`git show HEAD:…`), не по живому состоянию. Перед миграцией подтвердить, что на проде
   реально `bi_bi-pgdata`/`bi-pgdata` и project `bi` — иначе `up` создаст НОВЫЙ том (пустая метадата).
   Команда: `docker inspect bi-postgres --format '{{json .Mounts}}'` + `docker volume ls | grep bi`.
2. **Содержимое прод-`.env`** — см. первую находку (легаси `MB_SITE_URL`).
3. Реальный `make up ENV=prod` не запускался (правило — не раскатывать); проверена только сборка
   команд (`make -n`) и рендер конфигов.
