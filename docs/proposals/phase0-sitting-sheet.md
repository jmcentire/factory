# Phase 0 Sitting Sheet — founder go/no-go, one page

Remediation plan §0.7 (docs/proposals/remediation-plan-2026-08-30.md). Every item is a
checkbox: mark GO / a value / a veto. Ratified values become committed data
(acceptance_baseline.json, target-ABI packs) in the ratification commit — nothing here
takes effect from prose.

## 1. Wall-clock cap (the one number only you can set)

`signal_wall_clock_cap_hours` — the backstop on elapsed time since the last pass advance;
the only control that catches the zero-pass hung-lane class (the 127-hour disease).

Reference data (verified artifact citations in acceptance_baseline.json, extracted from
the four retained dogfood runs): first NO-relevant signals at 0.22 / 0.30 / 1.13 / 3.67 h;
**max healthy inter-pass gap 6.98 h** (ci-r1 overnight operator wait).

- **Proposed default: 24 h** cap — 3.4× the max healthy gap, dies 5× sooner than
  the 127-hour case. **Floor 12 h** (anything lower halts a healthy overnight
  run). Alternative: 72 h if you prefer a run-total-style backstop. (An earlier
  draft proposed a paired "12 h warn", but no wall-clock warn knob exists in the
  committed schema — WARN is pass-denominated per plan 0.4; ratifying a
  wall-clock warn would need a schema knob first, so it is not offered here.)
- [ ] 24 as proposed [ ] other value: ______

## 2. Pass knobs (already ruled — restated for the record)

`signal_pass_deadline = 4`, `signal_pass_warn = 3` (your 2026-08-30 ruling; GLM-5.3 best
by pass 4, success plummets by iteration 3). Both live in target-ABI data, frozen into
the generation tuple, bounded by max_attempts. Nothing to decide unless you revise.

- [ ] confirmed

## 3. Required-metric floor (round-3 G3)

Pins the reference corpus so it cannot be silently thinned before acceptance. Checker
support is pre-wired; ratifying adds this block to acceptance_baseline.json:

```json
"required_metrics": [
  {"metric": "first_no_relevant_signal_hours", "min_rows": 4},
  {"metric": "max_healthy_inter_pass_advance_gap_hours", "min_rows": 1}
]
```

- [ ] ratify as listed [ ] amend: ______

## 4. Greenfield acceptance target (Phase 6's graduation exercise)

You ruled: a new service for the Wander system, named when the factory updates are
ready. Phase 6 needs: the service named, a bounded closed spec, an executable oracle,
and a data-only target pack (which will also become the home of the two Wander
operational lines currently held in the ~/.claude/commands/validate.md loader).

- Service name: ______________________
- [ ] defer to the Phase 6 ratification sitting (non-blocking for Phases 1–5)

## 5. Docs defaults (rulings 5a–5d — you already said GO with refinements; sign here)

a. Glossary definitions anchored to canonical code referents (module::symbol +
   signature + digest), single-definition-site + referent-integrity checks — never
   paraphrase policing.
b. Standardized docstring format; first line = the local invariant; no line caps.
c. harness/*.py and scripts/*.py enter doctrine-parity scope.
d. Factory self-contained; external mirrors are thin pointers (done — reconciliation
   landed 2026-08-30).

- [ ] all four ratified [ ] veto: ______

## 6. Gate blast-tier assignment (one-time; contested → MAX until you downgrade)

All 24 registered gates (every gates.tsv row: A–Q, F3/F4/R2/R3, NOB, ACC, WPX) are proposed **Critical
tier** — the fail-closed default. Tier routing activates with Phase 5's endgame
tier-selection; until then every run pays full weight regardless, so downgrades here
cost nothing yet and can wait for real run data.

- [ ] all Critical for now [ ] downgrades: ______

## 7. Baseline citation sources

The 11 primary-artifact citations (paths + sha256 under ~/WanderRepos/repos, verified
live before commit) with the pre-instrumentation honesty caveat on every row.

- [ ] ratified as the acceptance baseline's reference corpus
