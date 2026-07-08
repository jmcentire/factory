PY ?= python3

.PHONY: help dev test lint typecheck check-purity ship

help:
	@echo "factory_core — make targets"
	@echo "  make dev           install the package + dev tooling (editable)"
	@echo "  make test          run the pytest suite"
	@echo "  make lint          ruff over factory_core / scripts / tests"
	@echo "  make typecheck     mypy over factory_core / scripts"
	@echo "  make check-purity  the anti-coupling guard (core imports nothing target-specific)"
	@echo "  make ship          run every gate, fail-closed (purity -> lint -> typecheck -> test)"

dev:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest tests/

lint:
	ruff check factory_core scripts tests

typecheck:
	mypy factory_core scripts

check-purity:
	$(PY) scripts/check_core_purity.py

# Fail-closed: `make` stops at the first non-zero gate, so `ship` is green only if every
# gate is green. Purity runs first — the boundary guarantee is the cheapest and most
# important check.
ship: check-purity lint typecheck test
	@echo "ship: all gates green (fail-closed)."
