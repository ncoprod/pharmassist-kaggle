.PHONY: help setup api-install api-dev api-lint web-install web-dev web-build web-lint lint build
.PHONY: e2e
.PHONY: validate

PYTHON ?= python3
VENV_DIR := .venv

help:
	@echo "Targets:"
	@echo "  setup       - Create venv, install API deps, install web deps"
	@echo "  api-dev     - Run FastAPI dev server"
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

api-lint: $(VENV_DIR)
	$(VENV_DIR)/bin/ruff check apps/api/src

web-install:
	npm ci

web-dev:
	npm -w apps/web run dev

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
