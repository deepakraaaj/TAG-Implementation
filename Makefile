# TAG Backend Makefile

DOMAIN ?=
CONFIG_FILE ?=
DB_URL ?=
DESCRIPTION ?=
METADATA_FILE ?=
PRIMARY_TABLE ?=
USER_TABLE ?=
LOCATION_TABLE ?=
OUTPUT_ROOT ?= domains
REPORT_FILE ?=
WRITE ?= 1
FORCE ?= 0
LLM ?= 0

.PHONY: up up-prod env-info down restart logs test test-pytest quality-gate benchmark-llm onboard-domain onboard-domain-help onboard-domain-config generate-domain clean

up:
	@printf '%s\n' "Using compose env file: $(CURDIR)/.env"
	docker compose --env-file .env up --build -d

up-prod:
	@printf '%s\n' "Using compose env file: $(CURDIR)/.env.production"
	docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up --build -d

env-info:
	@printf '%s\n' \
		"Development compose env file: $(CURDIR)/.env" \
		"Production compose env file: $(CURDIR)/.env.production"

down:
	docker compose down

restart:
	docker compose restart tag_backend

logs:
	docker compose logs -f tag_backend

test:
	pytest -q

test-pytest:
	pytest -q

quality-gate:
	pytest -q
	pytest -q tests/unit/api/test_chat_endpoint_stream_contract.py tests/unit/chat/test_chat_service_stream_completion.py tests/unit/chat/test_chat_service_timeouts.py

benchmark-llm:
	python3 scripts/benchmark_llm.py

onboard-domain:
	@if [ -z "$(DOMAIN)" ] && [ -z "$(CONFIG_FILE)" ]; then \
		$(MAKE) onboard-domain-help; \
	else \
		.venv/bin/python scripts/onboard_domain.py \
			$(if $(CONFIG_FILE),--config-file "$(CONFIG_FILE)",) \
			--domain "$(DOMAIN)" \
			$(if $(DB_URL),--db-url "$(DB_URL)",) \
			$(if $(DESCRIPTION),--description "$(DESCRIPTION)",) \
			$(if $(METADATA_FILE),--metadata-file "$(METADATA_FILE)",) \
			$(if $(PRIMARY_TABLE),--primary-table "$(PRIMARY_TABLE)",) \
			$(if $(USER_TABLE),--user-table "$(USER_TABLE)",) \
			$(if $(LOCATION_TABLE),--location-table "$(LOCATION_TABLE)",) \
			$(if $(OUTPUT_ROOT),--output-root "$(OUTPUT_ROOT)",) \
			$(if $(REPORT_FILE),--report-file "$(REPORT_FILE)",) \
			$(if $(filter 1 true yes,$(strip $(WRITE))),--write,) \
			$(if $(filter 1 true yes,$(strip $(FORCE))),--force,) \
			$(if $(filter 1 true yes,$(strip $(LLM))),--enable-llm-enhancement,); \
	fi

onboard-domain-config:
	@.venv/bin/python scripts/onboard_domain.py \
		--generate-config \
		$(if $(CONFIG_FILE),--config-file "$(CONFIG_FILE)",) \
		$(if $(DOMAIN),--domain "$(DOMAIN)",) \
		$(if $(DB_URL),--db-url "$(DB_URL)",) \
		$(if $(DESCRIPTION),--description "$(DESCRIPTION)",) \
		$(if $(METADATA_FILE),--metadata-file "$(METADATA_FILE)",) \
		$(if $(PRIMARY_TABLE),--primary-table "$(PRIMARY_TABLE)",) \
		$(if $(USER_TABLE),--user-table "$(USER_TABLE)",) \
		$(if $(LOCATION_TABLE),--location-table "$(LOCATION_TABLE)",) \
		$(if $(OUTPUT_ROOT),--output-root "$(OUTPUT_ROOT)",) \
		$(if $(REPORT_FILE),--report-file "$(REPORT_FILE)",) \
		$(if $(filter 1 true yes,$(strip $(WRITE))),--write,) \
		$(if $(filter 1 true yes,$(strip $(FORCE))),--force,) \
		$(if $(filter 1 true yes,$(strip $(LLM))),--enable-llm-enhancement,)

onboard-domain-help:
	@printf '%s\n' \
		'Usage:' \
		'  make onboard-domain DOMAIN=my_app' \
		'  make onboard-domain DOMAIN=my_app LLM=1' \
		'  make onboard-domain CONFIG_FILE=scripts/onboard_domain.request.json' \
		'  make onboard-domain DOMAIN=my_app DB_URL="mysql+aiomysql://..."' \
		'  make onboard-domain-config CONFIG_FILE=scripts/onboard_domain.request.json' \
		'' \
		'Defaults:' \
		'  DB_URL comes from .env DATABASE_URL when DB_URL is omitted.' \
		'  WRITE=1 writes files by default.' \
		'  Set FORCE=1 to overwrite an existing generated package.' \
	'' \
	'Optional vars:' \
	'  DESCRIPTION=... METADATA_FILE=... PRIMARY_TABLE=... USER_TABLE=... LOCATION_TABLE=...' \
	'  OUTPUT_ROOT=domains REPORT_FILE=...'

generate-domain:
	.venv/bin/python scripts/generate_domain.py --help

clean:
	find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" -o -name ".ruff_cache" \) -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -f .coverage
