# bash, not zsh: CI runners ship bash but not zsh.
SHELL := /bin/bash

# ──────────────────────────────────────────────────────────────────────────────
# Lemma CLI — local developer commands
#
#   make test         run unit + e2e tests
#   make test-unit    fast unit tests (fake SDK clients; no network, no docker)
#   make test-e2e     end-to-end tests (real backend + docker: postgres/redis/
#                     supertokens). Drives the CLI over TCP against a subprocess
#                     uvicorn server. Requires docker.
#   make coverage     unit coverage report
#   make lint         ruff check
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: help test test-unit test-e2e coverage lint

help:
	@echo "Lemma CLI:"
	@echo "  make test          run unit + e2e tests"
	@echo "  make test-unit     fast unit tests (no docker)"
	@echo "  make test-e2e      e2e tests (real backend + docker)"
	@echo "  make coverage      unit coverage report"
	@echo "  make lint          ruff check"

test: test-unit test-e2e
	@echo ""
	@echo "✓ lemma-cli unit + e2e tests complete."

test-unit:
	@echo "→ lemma-cli unit tests…"
	uv run pytest -m "not e2e" -q

test-e2e:
	@echo "→ lemma-cli e2e tests (real backend + docker)…"
	uv run pytest -m e2e -q

coverage:
	@echo "→ lemma-cli unit coverage…"
	uv run --with pytest-cov pytest -m "not e2e" \
		--cov=lemma_cli --cov-report=term-missing -q

lint:
	uv run ruff check .
