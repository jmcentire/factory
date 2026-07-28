PY ?= python3

.PHONY: help dev test test-isolation test-tessera lint typecheck check-purity check-doctrine ship

FACTORY_TESSERA_BIN ?= ../tessera/target/release/tessera

help:
	@echo "factory_core — make targets"
	@echo "  make dev           install the package + dev tooling (editable)"
	@echo "  make test          run the pytest suite"
	@echo "  make test-isolation prove enforced Coder/Tester separation on macOS"
	@echo "  make test-tessera  run the real Tessera CLI integration proof"
	@echo "  make lint          ruff over factory_core / scripts / tests"
	@echo "  make typecheck     mypy over factory_core / scripts"
	@echo "  make check-purity  the anti-coupling guard (core imports nothing target-specific)"
	@echo "  make check-doctrine structural parity for active doctrine surfaces"
	@echo "  make ship          run every gate (purity -> doctrine -> lint -> typecheck -> test)"

dev:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest tests/

test-isolation:
	@$(PY) -c 'import platform, sys; sys.exit(0 if platform.system() == "Darwin" else 1)' || \
		(echo "enforced isolation proof requires macOS Seatbelt" >&2; exit 1)
	$(PY) -m pytest -m isolation_integration tests/test_isolated_build_loop.py

test-tessera:
	@test -x "$(FACTORY_TESSERA_BIN)" || \
		(echo "Tessera binary missing or not executable: $(FACTORY_TESSERA_BIN)" >&2; exit 1)
	FACTORY_TESSERA_BIN="$(FACTORY_TESSERA_BIN)" \
		$(PY) -m pytest tests/test_tessera_cli_integration.py

lint:
	ruff check factory_core factory_runtime scripts tests

typecheck:
	mypy factory_core factory_runtime scripts

check-purity:
	$(PY) scripts/check_core_purity.py

check-doctrine:
	$(PY) scripts/check_doctrine_sync.py

# Fail-closed: `make` stops at the first non-zero gate, so `ship` is green only if every
# gate is green. Purity runs first — the boundary guarantee is the cheapest and most
# important check.
ship: check-purity check-doctrine lint typecheck test
	@echo "ship: all gates green (fail-closed)."
