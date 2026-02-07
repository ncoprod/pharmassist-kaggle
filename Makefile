.PHONY: help setup api-install api-dev api-dev-full api-lint web-install web-dev web-dev-full web-build web-lint lint build
.PHONY: e2e redteam security-audit
.PHONY: validate

PYTHON ?= python3
VENV_DIR := .venv
DEMO_DB_PATH ?= /tmp/pharmassist_full.db
DEMO_DATA_DIR ?= /tmp/paris15_full
DEMO_API_KEY ?= change-me
DEMO_ADMIN_API_KEY ?= change-me
DEMO_WEB_PORT ?= 5173

help:
	@echo "Targets:"
	@echo "  setup       - Create venv, install API deps, install web deps"
	@echo "  api-dev     - Run FastAPI dev server"
	@echo "  api-dev-full- Run FastAPI dev server on full demo dataset"
	@echo "  web-dev-full- Run web dev server preconfigured for api-dev-full"
	@echo "  lint        - Lint API + web"
	@echo "  build       - Build web"

setup: api-install web-install

$(VENV_DIR):
	$(PYTHON) -m venv $(VENV_DIR)

api-install: $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --upgrade pip
	$(VENV_DIR)/bin/pip install -e "apps/api[dev]"

api-dev: $(VENV_DIR)
	$(VENV_DIR)/bin/uvicorn pharmassist_api.main:app --app-dir apps/api/src --reload --port 8000

api-dev-full: $(VENV_DIR)
	PHARMASSIST_DB_PATH='$(DEMO_DB_PATH)' PHARMASSIST_PHARMACY_DATA_DIR='$(DEMO_DATA_DIR)' PHARMASSIST_API_KEY='$(DEMO_API_KEY)' PHARMASSIST_ADMIN_API_KEY='$(DEMO_ADMIN_API_KEY)' $(VENV_DIR)/bin/uvicorn pharmassist_api.main:app --app-dir apps/api/src --reload --port 8000

api-lint: $(VENV_DIR)
	$(VENV_DIR)/bin/ruff check apps/api/src

web-install:
	npm ci

web-dev:
	npm -w apps/web run dev

web-dev-full:
	VITE_API_BASE_URL='http://localhost:8000' VITE_API_KEY='$(DEMO_API_KEY)' VITE_ADMIN_DB_PREVIEW_KEY='$(DEMO_ADMIN_API_KEY)' npm -w apps/web run dev -- --host 127.0.0.1 --port $(DEMO_WEB_PORT)

web-build:
	npm -w apps/web run build

web-lint:
	npm -w apps/web run lint

lint: api-lint web-lint

build: web-build

validate: $(VENV_DIR)
	$(VENV_DIR)/bin/python -m pharmassist_api.scripts.validate_contracts

e2e: $(VENV_DIR)
	./scripts/e2e.sh

redteam: $(VENV_DIR)
	./scripts/redteam_check.sh

security-audit: $(VENV_DIR)
	$(VENV_DIR)/bin/python -m pip install -q pip-audit bandit
	$(VENV_DIR)/bin/pip-audit
	$(VENV_DIR)/bin/bandit -q -r apps/api/src -lll
	npm audit --omit=dev --audit-level=high
