# Convenience targets. `make` is not available by default on Windows —
# scripts/setup.ps1 and the npm scripts cover the same ground there.
.PHONY: help setup up down test lint build zip verify

help:
	@echo "setup   install API and web dependencies"
	@echo "up      docker compose up --build (no Ollama, no model weights)"
	@echo "down    stop the stack"
	@echo "test    run API and web test suites"
	@echo "lint    ruff + mypy + tsc"
	@echo "build   production build of the web app"
	@echo "zip     build the submission archive"
	@echo "verify  check the submission archive from a clean extraction"

setup:
	cd apps/api && python -m venv .venv && ./.venv/bin/python -m pip install -e ".[dev,pdf]"
	cd apps/api && ./.venv/bin/python -m playwright install chromium
	cd apps/web && npm install

up:
	docker compose up --build

down:
	docker compose down

test:
	cd apps/api && ./.venv/bin/python -m pytest -q -m "not live"
	cd apps/web && npm run test

lint:
	cd apps/api && ./.venv/bin/python -m ruff check app tests
	cd apps/web && npm run typecheck

build:
	cd apps/web && npm run build

zip:
	bash scripts/build-submission-zip.sh

verify:
	bash scripts/verify-submission.sh
