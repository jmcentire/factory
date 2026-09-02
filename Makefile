.PHONY: help dev venv clean-venv check-python show-python test test-isolation test-tessera lint typecheck check-purity check-doctrine check-wiring check-authority check-harness check-denial-probes check-acceptance ship

.DEFAULT_GOAL := help

FACTORY_TESSERA_BIN ?= ../tessera/target/release/tessera

# The interpreter floor. `pyproject.toml` (requires-python) is the authority; this mirrors it so
# the gates can refuse a wrong interpreter *before* the failure surfaces as a confusing
# ModuleNotFoundError deep inside a guard script.
PY_MIN_MAJOR ?= 3
PY_MIN_MINOR ?= 12
PY_PREFERRED := python$(PY_MIN_MAJOR).$(PY_MIN_MINOR)

# One implementation of the floor check, so the bootstrap interpreter and the interpreter the
# gates actually run on are judged by identical rules and report in identical language.
#   $(1) the interpreter to test    $(2) how to name it in the verdict
define assert_interpreter
if $(1) -c 'import sys; sys.exit(0 if sys.version_info[:2] >= ($(PY_MIN_MAJOR), $(PY_MIN_MINOR)) else 1)' 2>/dev/null; then \
	echo "check_python: GREEN — $(2) ($$($(1) -V 2>/dev/null)) satisfies the >= $(PY_MIN_MAJOR).$(PY_MIN_MINOR) floor declared in pyproject.toml"; \
else \
	echo "check_python: RED — $(2) ($$($(1) -V 2>/dev/null || echo 'not runnable')) does not satisfy the required Python >= $(PY_MIN_MAJOR).$(PY_MIN_MINOR)" >&2; \
	command -v $(PY_PREFERRED) >/dev/null 2>&1 || \
		echo "  $(PY_PREFERRED) is not on PATH, so detection fell back to python3" >&2; \
	echo "  Install $(PY_PREFERRED), or set PY explicitly: make PY=/path/to/python ship" >&2; \
	exit 1; \
fi
endef

# Fail closed at parse time if that macro ever goes missing or is renamed. This is not paranoia:
# when a `$(call ...)` is a recipe's only line and expands to nothing, GNU make drops the recipe
# and reports "Nothing to be done" — with exit status 0. `check-python` is a prerequisite of
# every other gate, so an evaporated recipe would leave the interpreter floor unenforced while
# `make ship` still reported success. A guard that can silently become a no-op is not a guard.
ifeq ($(strip $(assert_interpreter)),)
  $(error assert_interpreter is empty — the interpreter floor would not be enforced; refusing to run)
endif

# Prefer the exact declared version, fall back to `python3`. Both are resolved through PATH, so
# an activated venv wins over a system install — we prefer a conforming interpreter, not an
# escape from the environment the contributor chose. `check-python` still gates whatever comes
# out of this, so a wrong fallback fails loudly rather than silently.
PY_BOOTSTRAP := $(shell command -v $(PY_PREFERRED) >/dev/null 2>&1 && echo $(PY_PREFERRED) || echo python3)

# --- Where the gates run -------------------------------------------------------------------
#
# Locally, every target runs out of a repo-managed virtualenv that Make creates and keeps in
# sync, so a contributor never has to remember an activation step and two contributors cannot
# silently be testing against different dependency sets. `make ship` and CI then differ only in
# where the interpreter came from.
#
# The venv is NOT managed when either is true:
#   · CI is set — the runner already provisions and installs into its own interpreter, and
#     re-doing that in a venv would double install time and change a working pipeline.
#   · PY was set on the command line or in the environment — an explicit choice outranks ours.
#
# `make show-python` reports which branch is in force.
VENV ?= .venv
VENV_STAMP := $(VENV)/.factory-deps

ifneq ($(filter command line environment,$(origin PY)),)
  PY_SOURCE := explicit PY override
  VENV_PREREQ :=
else ifneq ($(strip $(CI)),)
  PY := $(PY_BOOTSTRAP)
  PY_SOURCE := CI interpreter (venv management disabled because CI is set)
  VENV_PREREQ :=
else
  PY := $(abspath $(VENV))/bin/python
  PY_SOURCE := repo-managed venv at $(VENV)
  VENV_PREREQ := $(VENV_STAMP)
endif

# Re-provision whenever the dependency declaration moves. The stamp is inside the venv, so
# deleting the venv also forces a rebuild, and `pyproject.toml` being newer than the stamp is
# exactly the condition that means "installed set is stale".
$(VENV_STAMP): pyproject.toml
	@$(call assert_interpreter,$(PY_BOOTSTRAP),bootstrap $(PY_BOOTSTRAP))
	@test -x "$(VENV)/bin/python" || \
		(echo "venv: creating $(VENV) from $(PY_BOOTSTRAP)"; $(PY_BOOTSTRAP) -m venv "$(VENV)")
	@echo "venv: syncing dependencies from pyproject.toml"
	@"$(VENV)/bin/python" -m pip install --quiet --upgrade pip
	@"$(VENV)/bin/python" -m pip install --quiet -e ".[dev]"
	@touch "$@"
	@echo "venv: GREEN — $(VENV) is in sync with pyproject.toml"

venv: $(VENV_PREREQ) ## create or refresh the repo-managed virtualenv
ifeq ($(strip $(VENV_PREREQ)),)
	@echo "venv: skipped — $(PY_SOURCE)"
else
	@echo "venv: ready at $(VENV) ($$($(PY) -V 2>/dev/null))"
endif

clean-venv: ## delete the repo-managed virtualenv
	rm -rf "$(VENV)"

dev: venv ## install the package + dev tooling (editable)
ifneq ($(strip $(VENV_PREREQ)),)
	@echo "dev: $(VENV) is ready; no activation needed — make targets use it automatically"
else
	$(PY) -m pip install -e ".[dev]"
endif

# Fail closed on the interpreter itself. Without this, a pre-3.12 `python3` reaches
# check_core_purity.py and dies on `import tomllib` — a stdlib error message that says nothing
# about the real cause, on the gate a contributor is most likely to run first. Reaching the error
# branch means detection already tried $(PY_PREFERRED) and fell back, so say so: the fix is
# usually to install that version, not to go hunting.
check-python: $(VENV_PREREQ) ## refuse an interpreter below the declared floor
	@$(call assert_interpreter,$(PY),PY=$(PY))

# Which interpreter the gates will use, and why. First thing to run when a gate fails for
# reasons that smell like the wrong environment.
show-python: ## report which interpreter the gates will use, and why
	@echo "PY_PREFERRED  = $(PY_PREFERRED) (floor: >= $(PY_MIN_MAJOR).$(PY_MIN_MINOR))"
	@echo "PY_BOOTSTRAP  = $(PY_BOOTSTRAP) ($$($(PY_BOOTSTRAP) -V 2>/dev/null || echo 'not runnable'))"
	@echo "PY            = $(PY)"
	@echo "source        = $(PY_SOURCE)"
	@echo "version       = $$($(PY) -V 2>/dev/null || echo 'not runnable')"
	@echo "venv managed  = $(if $(strip $(VENV_PREREQ)),yes ($(VENV)),no)"

test: check-python ## run the pytest suite
	$(PY) -m pytest tests/

test-isolation: check-python ## prove enforced Coder/Tester separation on macOS
	@$(PY) -c 'import platform, sys; sys.exit(0 if platform.system() == "Darwin" else 1)' || \
		(echo "enforced isolation proof requires macOS Seatbelt" >&2; exit 1)
	$(PY) -m pytest -m isolation_integration tests/test_isolated_build_loop.py

test-tessera: check-python ## run the real Tessera CLI integration proof
	@test -x "$(FACTORY_TESSERA_BIN)" || \
		(echo "Tessera binary missing or not executable: $(FACTORY_TESSERA_BIN)" >&2; exit 1)
	FACTORY_TESSERA_BIN="$(FACTORY_TESSERA_BIN)" \
		$(PY) -m pytest tests/test_tessera_cli_integration.py

lint: check-python ## ruff over core/runtime/scripts/tests and executable Python harness controls
	$(PY) -m ruff check factory_core factory_runtime scripts tests \
		harness/agreement_contract.py harness/agreement_probe.py \
		harness/attention_gate.py harness/codex_lane_session.py harness/dispatcher.py \
		harness/lane_dialogue.py harness/lane_repository.py harness/orchestrator_channel.py \
		harness/semantic_union.py \
		harness/supervise_advisory.py harness/watchdog.py

typecheck: check-python ## mypy over core/runtime/scripts and executable Python harness controls
	$(PY) -m mypy factory_core factory_runtime scripts \
		harness/agreement_contract.py harness/agreement_probe.py \
		harness/attention_gate.py harness/codex_lane_session.py harness/dispatcher.py \
		harness/lane_dialogue.py harness/lane_repository.py harness/orchestrator_channel.py \
		harness/semantic_union.py \
		harness/supervise_advisory.py harness/watchdog.py

check-purity: check-python ## the anti-coupling guard (core imports nothing target-specific)
	$(PY) scripts/check_core_purity.py

check-doctrine: check-python ## structural parity for active doctrine surfaces
	$(PY) scripts/check_doctrine_sync.py

check-wiring: check-python ## fail-closed wiring audit (every provided service reachable from an entrypoint)
	$(PY) scripts/check_wiring.py

check-authority: check-python ## ban exemplar's TesseraSeal and signet-sdk's authority seam
	$(PY) scripts/check_forbidden_authority.py

# The harness gate is syntactic + chain-integrity only: scripts parse, are executable,
# and any local directive ledger verifies. Behavioral enforcement (forced-negative
# drills) lives in tests/test_harness_scripts.py and runs under `test`.
check-harness: check-python ## harness scripts parse/executable; local ledger chains verify
	@bash -n harness/*.sh
	@$(PY) -m py_compile harness/*.py
	@for s in harness/*.sh; do test -x "$$s" || { echo "not executable: $$s" >&2; exit 1; }; done
	@if [ -f DIRECTIVES/ledger.jsonl ] || [ -f DIRECTIVES/provisional.jsonl ]; then \
		DIRECTIVE_LEDGER=DIRECTIVES/ledger.jsonl $(PY) harness/directive.py verify; fi
	@echo "check-harness: GREEN — harness scripts parse; ledger chains verify"

# Gate I (control-structure plan slice 6): no factory gate ships without a registered,
# collecting denial probe. The registry (harness/gates.tsv) maps each gate to the pytest
# node-ids that prove it blocks the prohibited action; this check fails the build when a gate
# has no probe, a probe is a dead pointer, or a gate has no declared red_now. A gate without a
# passing probe is theater. `make test` then proves the registered probes pass.
check-denial-probes: check-python ## every factory gate has a registered, collecting denial probe
	@$(PY) scripts/check_denial_probes.py

check-acceptance: check-python ## removal-ledger tree claims, exhaustive kind classification, baseline citations
	@$(PY) scripts/check_acceptance.py

check-glossary: check-python ## glossary referent-integrity + single-definition-site (plan 5.2)
	$(PY) scripts/check_glossary.py

# Fail-closed: `make` stops at the first non-zero gate, so `ship` is green only if every
# gate is green. Purity runs first — the boundary guarantee is the cheapest and most
# important check.
ship: check-purity check-doctrine check-wiring check-authority check-harness check-denial-probes check-acceptance check-glossary lint typecheck test ## run every gate (purity -> doctrine -> wiring -> authority -> harness -> denial-probes -> acceptance -> lint -> typecheck -> test)
	@echo "ship: all gates green (fail-closed)."

help:
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_\/-]+:.*## / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort | grep -v '#'
