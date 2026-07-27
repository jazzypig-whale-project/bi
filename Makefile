# BI-стек: docker + compose + make. Стоковый metabase + свой postgres-метадата; на проде ещё
# nginx-tls (build, TLS DNS-01). Один запуск `make up`, режим выбирается `ENV=local|prod` (дефолт
# local) — разница между режимами в наборе compose-файлов, не в отдельных целях. Конфиг — в
# docker-compose.makefile*.yaml (дефолты ${VAR:-...}); `.env` — ТОЛЬКО секреты (см. .env.example).
# Compose-файлы с инфиксом `.makefile.` — гоняются только через make (не автодетектятся docker'ом).
BUILD_DATE ?= $(shell date -u +"%Y-%m-%dT%H:%M:%S+00:00")
VCS_REF    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
VERSION    ?= $(shell git describe --tags --always --abbrev=7 2>/dev/null || echo dev)
DOCKER_BUILDKIT ?= 1

ENV ?= local
export ENV

DOCKER_COMPOSE := \
	DOCKER_BUILDKIT="$(DOCKER_BUILDKIT)" \
	COMPOSE_DOCKER_CLI_BUILD="$(DOCKER_BUILDKIT)" \
	BUILD_DATE="$(BUILD_DATE)" \
	VCS_REF="$(VCS_REF)" \
	VERSION="$(VERSION)" \
	docker compose

BASE := -f docker-compose.makefile.yaml
ifeq ($(ENV),prod)
FILES := $(BASE) -f docker-compose.makefile.prod.yaml
else
FILES := $(BASE) -f docker-compose.makefile.local.yaml
endif

.PHONY: help
help: ## Print help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "%-16s %s\n", $$1, $$2}' | sort

# --- Запуск (ENV=local|prod, дефолт local). Порядок держат depends_on + вложенные цели ниже ---
.PHONY: up up-bi-postgres up-metabase up-nginx-tls
up: ## Start the stack (ENV=local|prod, default local); waits each service healthy
	@scripts/preflight.sh $(ENV)
ifeq ($(ENV),prod)
	@$(MAKE) --no-print-directory up-nginx-tls
else
	@$(MAKE) --no-print-directory up-metabase
endif

up-bi-postgres: ## Start metadata Postgres (waits healthy)
	@$(DOCKER_COMPOSE) $(FILES) up -d --wait bi-postgres

up-metabase: up-bi-postgres ## Start Metabase after Postgres (waits healthy)
	@$(DOCKER_COMPOSE) $(FILES) up -d --wait metabase

up-nginx-tls: up-metabase ## Start nginx-tls after Metabase (ENV=prod; renders cert config)
	@scripts/render-tls-config.sh
	@$(DOCKER_COMPOSE) $(FILES) up -d --wait nginx-tls

# --- Сборка образа nginx-tls (nginx-certbot + плагин Spaceship DNS-01), отдельно от запуска ---
.PHONY: build
build: ## Build the nginx-tls image (nginx-certbot + Spaceship DNS-01 plugin)
	@$(DOCKER_COMPOSE) $(BASE) -f docker-compose.makefile.prod.yaml build nginx-tls

# --- Обслуживание ---
.PHONY: config htpasswd backup restore
config: ## Render final compose (ENV=local|prod, default local)
	@$(DOCKER_COMPOSE) $(FILES) config

htpasswd: ## Add/replace a basic-auth user for prod nginx-tls (USER=name; hidden input; keeps others)
	@scripts/htpasswd-add.sh "$(USER)"

backup: ## Encrypted dump of metadata DB → backups/ (needs BI_BACKUP_ENCRYPT_PASSWORD in .env)
	@scripts/db-backup.sh

restore: ## ⚠ DESTRUCTIVE: restore metadata from backups/FILE (FILE=metabase_db_...enc)
	@scripts/db-restore.sh "$(FILE)"

# --- Останов (внизу: down редкий, destroy разрушающий) ---
.PHONY: down destroy
down: ## Stop and remove containers of both modes (metadata volume kept)
	@$(DOCKER_COMPOSE) $(BASE) -f docker-compose.makefile.local.yaml -f docker-compose.makefile.prod.yaml down --remove-orphans

destroy: ## ⚠ DESTRUCTIVE: down + drop the metadata volume (bi-pgdata) — Metabase data lost
	@$(DOCKER_COMPOSE) $(BASE) down -v
