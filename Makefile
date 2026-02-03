.PHONY: help setup api-install api-dev api-lint web-install web-dev web-build web-lint lint build

PYTHON ?= python3
VENV_DIR := .venv

help:
\t@echo "Targets:"
\t@echo "  setup       - Create venv, install API deps, install web deps"
\t@echo "  api-dev     - Run FastAPI dev server"
\t@echo "  lint        - Lint API + web"
\t@echo "  build       - Build web"

setup: api-install web-install

$(VENV_DIR):
\t$(PYTHON) -m venv $(VENV_DIR)

api-install: $(VENV_DIR)
\t$(VENV_DIR)/bin/pip install --upgrade pip
\t$(VENV_DIR)/bin/pip install -e "apps/api[dev]"

api-dev: $(VENV_DIR)
\t$(VENV_DIR)/bin/uvicorn pharmassist_api.main:app --app-dir apps/api/src --reload --port 8000

api-lint: $(VENV_DIR)
\t$(VENV_DIR)/bin/ruff check apps/api/src

web-install:
\tnpm install

web-dev:
\tnpm -w apps/web run dev

web-build:
\tnpm -w apps/web run build

web-lint:
\tnpm -w apps/web run lint

lint: api-lint web-lint

build: web-build

