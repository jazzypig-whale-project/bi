# CLAUDE.md — bi (Metabase analytics for the orchestrator app)

Quick orientation for future sessions. Task details are in `plans/plan.md`, research in `plans/research/`.

## ⛔ HARD CONSTRAINT — NEVER ENDANGER THE APP'S PRODUCTION DATABASE

The app's production DB is PostgreSQL `orchestrator` (container `orchestrator-postgres-1` on host
`api.apple-certificate.solutions`). Metabase reads it, and ONLY like this:

- **Read-only role `metabase`** — `CONNECT` + `USAGE` + `SELECT` only. NEVER the `orchestrator`
  superuser, never write/DDL. Verified on prod: not superuser, no createdb/createrole/bypassrls.
- **Guards on the role** — `statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`,
  `lock_timeout=15s`, plus `CONNECTION LIMIT 20` — so a heavy/stuck Metabase query cannot hold
  connections, lock the app, or exhaust its pool (`max_connections=100`).
- **Metabase metadata lives in its OWN separate Postgres** (`bi-postgres` in this compose), NEVER in
  `orchestrator`. H2 is forbidden in prod. Metabase never migrates or writes to the app DB.
- **The app DB port is never published** (loopback-only `127.0.0.1:5432`); Metabase reaches it over the
  app's docker network (`orchestrator_default`, host `postgres:5432`), not an exposed port.
- On prod the read-only role is created **by hand** (`docker exec … psql`), NOT by deploying the
  orchestrator app from `main` (main may carry unreleased app changes). Its password is mirrored into
  `/opt/orchestrator/.env` `METABASE_DB_PASSWORD` so a future main deploy reconciles it, not breaks it.
- Anything that could touch `orchestrator` on write / schema / load — **don't; ask the owner.**

## What this is

A self-contained docker-compose BI stack: **Metabase** + a **dedicated Postgres for its metadata** +
**nginx (basic-auth / TLS)**. Deployed co-located with the app on `api.apple-certificate.solutions`,
served externally on **:8443** (real Let's Encrypt cert, DNS-01 via Spaceship). Access comes from
**varying IPs**, so the gate is **basic-auth in nginx** plus Metabase's own login — not a network ACL.
The host firewall rule (`:8443` open to any source) lives in the neighbouring infra repo, not here.

## Modes — exactly two (dev decommissioned)

- **local** — tests on a workstation: plain nginx on loopback `:8080`, NO app-DB. `make up`.
- **prod** — co-located on api: nginx-tls `:8443` + metabase joined to the app network for read-only
  reads of `orchestrator`. `make up ENV=prod`.

Config lives in `docker-compose*.yaml` (defaults `${VAR:-...}`); `.env` holds ONLY secrets
(`MB_DB_PASS`, `BI_BACKUP_ENCRYPT_PASSWORD`, `SPACESHIP_API_KEY`/`SPACESHIP_API_SECRET`).

## Layout

- `docker-compose.yaml` (base: metabase + bi-postgres) + `docker-compose.local.yaml` (plain nginx) +
  `docker-compose.prod.yaml` (nginx-tls + app-network + prod env). No compose profiles.
- `scripts/` — all bash wrappers (preflight, render-tls-config, htpasswd-add, db-backup, db-restore);
  Makefile recipes are thin and just call these.
- `nginx/` — TLS vhost template + certbot Dockerfile. `scripts/db/` — in-container dump/restore payload.
- `plans/` — the single task list (`plan.md`); `docs/` — connect-datasource guide + `mbc` reference.
- `mbc` / `mbcode/` / `cards/` / `dashboards/` / `collections/` / `.state/` — Metabase as code, see below.

## Metabase as code

Dashboards and questions on the live Metabase instance are managed as YAML files here, merged in
(history preserved) from a former sibling repo. Full reference: `docs/metabase-as-code.md`.

- `./mbc export|validate|diff|apply` — `mbc` resolves everything relative to itself (`--dir` = the
  directory containing it, `.env` = `<dir>/.env`), so it works from the repo root with zero changes.
  Also wired into `make mbc-validate` / `make mbc-diff` / `make mbc-test`.
- Reads `.env` for `METABASE_BASE_URL` / `METABASE_BASIC_USERNAME` / `METABASE_BASIC_PASSWORD` /
  `METABASE_API_KEY` — the same file as the bi-stack secrets (`MB_DB_PASS` etc.), all required.
- **`.state/<host>.yaml` MUST be committed.** It maps logical keys to server ids/entity_ids; lose
  it and the next `apply` cannot recognize existing entities and recreates all of them as duplicates.
- **`apply` replaces a dashboard's dashcards wholesale** (`PUT /api/dashboard/:id` has no partial
  mode). A dashcard added through the Metabase UI is deleted by the next `apply` unless adopted
  first with `./mbc export --overwrite`.
- Needs its own Python venv (`.venv`, gitignored — absolute shebangs, not portable): `python3 -m venv
  .venv && .venv/bin/pip install --requirement requirements.txt pytest`.

## Commands

- `make up` — local: bring up `bi-postgres` → `metabase` → `nginx` in order (each waits healthy).
- `make up ENV=prod` — prod: preflight + render TLS config + up by dependency order (TLS `:8443`).
- `make htpasswd USER=<name>` — add/replace a basic-auth user (hidden input; keeps other users).
- `make down` — stop/remove containers (metadata volume kept); `make destroy` — ⚠ also drops the volume.
- `make backup` / `make restore FILE=…` — encrypted metadata dump / restore.
- `make ps` / `make config` / `make logs [F=svc]` / `make logs-nginx` (`MODE=local|prod`, default local).

## Conventions / gotchas

- **`MB_SITE_URL` on prod MUST carry `:8443`** — without the port Metabase redirects to `:443` → 403
  from the app's nginx. It is set per-mode in the compose overrides, not in `.env`.
- **Never `make destroy` / `down -v` on prod** — it drops the metadata volume (all Metabase config/dashboards).
- **Entropy fix** `/dev/urandom:/dev/random` (JVM start otherwise blocks on `/dev/random`); JVM capped
  `-Xmx1g` + cgroup `mem_limit` (idle footprint ~1.1 GiB, measured).
- **nginx reads htpasswd per request** (bind-mounted by path) → no reload needed after `make htpasswd`.
- **Plans live in `plans/`** — `plans/plan.md` is the single task list (top-down by priority); no second list.
- **Secrets NEVER in git:** `.env`, `nginx/creds.htpasswd`, `nginx/spaceship.ini`, the backup key — all
  `.gitignore`d. The repo carries only `.env.example` / `creds.htpasswd.example`.
- **Commit and push ONLY with the owner's explicit permission in the same message.**
- **committed ≠ deployed** — code in the repo is not live until a deliberate `make up ENV=prod`.
- Origin: `git@github.com:jazzypig-whale-project/bi.git`, branch `main`. Host / firewall / tunnels live
  in the neighbouring infra repo (Ansible-IaC).
