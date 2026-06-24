# bash, not zsh: CI runners (Ubuntu) ship bash but not zsh, and the recipes use
# no zsh-only syntax. zsh here made `make test-unit` fail with Error 127 in CI.
SHELL := /bin/bash

TEAMS_MANIFEST_DIR ?= manifests/teams
OUTPUT ?= $(TEAMS_MANIFEST_DIR)/lemma-teams.zip
# Parallelism for the fast e2e suite (pytest-xdist). Each worker spins up its
# OWN isolated testcontainers (Postgres/Redis/SuperTokens) on ephemeral ports
# and per-worker /tmp roots, so workers never share state. Default 2 is the
# safe, verified-green value (each worker loads a local embedding model + its
# own container trio, so workers are RAM-hungry — Kreuzberg gets OOM-killed if
# the host runs out of memory). Bump on roomy machines: E2E_WORKERS=4 (~2x) or
# E2E_WORKERS=auto (one per core); E2E_WORKERS=0 disables (in-process serial).
# Only the fast suite is parallelized; the runtime suite (real streaq worker +
# docker workspace runtime) shares a global container namespace and stays serial.
E2E_WORKERS ?= 2
E2E_PARALLEL := -n $(E2E_WORKERS) --dist loadscope

.PHONY: help test test-unit test-e2e test-e2e-fast test-e2e-runtime test-module coverage coverage-unit coverage-e2e test-collect lint migrate docker-build docker-build-cloud docker-build-arm64 docker-build-amd64 agentbox-build teams-manifest-zip

help:
	@echo "Lemma backend:"
	@echo "  make test              # run backend tests"
	@echo "  make test-unit         # run non-e2e tests"
	@echo "  make test-e2e          # run all e2e tests"
	@echo "  make test-e2e-fast     # fast API e2e (no slow/worker/workspace/provider/local_cli),"
	@echo "                         #   parallel via pytest-xdist (E2E_WORKERS=$(E2E_WORKERS); auto|0 to override)"
	@echo "  make test-e2e-runtime  # run slow worker/workspace/local/provider e2e tests (serial)"
	@echo "  make test-module MODULE=pod"
	@echo "  make coverage-unit     # unit coverage"
	@echo "  make coverage-e2e      # e2e coverage"
	@echo "  make lint              # run ruff checks"
	@echo "  make migrate           # apply database migrations"
	@echo "  make docker-build-cloud  # build the lemma-cloud image"
	@echo "  (full dev workflow: use 'make dev STACK=cloud|oss' from the repo root)"
	@echo "  make teams-manifest-zip OUTPUT=path"
	@echo "                         # package Teams manifest zip"
	@echo "  make docker-build      # build backend image from monorepo root"
	@echo "  make agentbox-build # build local AgentBox runtime images"
	@echo ""
	@echo "Run app-level commands such as setup/start/logs from the repository root."

test:
	uv run pytest

test-unit:
	uv run pytest -m "not e2e"

test-e2e:
	uv run pytest -m e2e

test-e2e-fast:
	uv run pytest $(E2E_PARALLEL) -m "e2e and not slow and not worker and not workspace and not provider and not local_cli"

test-e2e-runtime:
	uv run pytest -m "e2e and (slow or worker or workspace or provider or local_cli)"

test-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make test-module MODULE=pod" && exit 1)
	uv run pytest app/modules/$(MODULE)

coverage: coverage-unit coverage-e2e

coverage-unit:
	uv run pytest -m "not e2e" --cov=app --cov-report=term-missing --cov-report=xml:coverage-unit.xml

coverage-e2e:
	uv run pytest -m e2e --cov=app --cov-report=term-missing --cov-report=xml:coverage-e2e.xml

coverage-module:
	@test -n "$(MODULE)" || (echo "MODULE is required, e.g. make coverage-module MODULE=agent" && exit 1)
	uv run pytest app/modules/$(MODULE) \
		--cov=app/modules/$(MODULE) \
		--cov-report=term-missing \
		--cov-fail-under=90

test-collect:
	uv run pytest --collect-only -q

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head

# NOTE: the full dev workflow (infra + app + frontend, per stack, properly
# port-isolated) lives in the repo-root Makefile: `make dev STACK=cloud|oss`.
# Don't add a `dev` target here — it shadowed the root one and bound the wrong
# ports.

docker-build:
	cd .. && docker build -f lemma-backend/Dockerfile -t lemma-backend:local .

docker-build-cloud:
	cd .. && docker build -f lemma-cloud/Dockerfile -t lemma-cloud:local .

docker-build-arm64:
	cd .. && docker buildx build --platform linux/arm64 -f lemma-backend/Dockerfile -t lemma-backend:local .

docker-build-amd64:
	cd .. && docker buildx build --platform linux/amd64 -f lemma-backend/Dockerfile -t lemma-backend:local .

agentbox-build:
	$(MAKE) -C ../agentbox build

teams-manifest-zip:
	python3 scripts/package_teams_manifest.py \
		--manifest-dir "$(TEAMS_MANIFEST_DIR)" \
		--output "$(OUTPUT)"

