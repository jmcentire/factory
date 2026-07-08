# Provenance sync log — generic advances synced from the origin/reference target

**Purpose.** `factory_core` is the founder-owned, portable, generic software-factory
core. The **origin/reference target** — the first consuming target the factory serves — is
where new factory disciplines get proven under load before they are known to be generic.
This document is the standing record of which of those advances are *generic*, which have
been extracted into `factory_core` in **pure form** (naming no target, importing no target
code), which remain planned, and the repeatable mechanism by which future target-side
factory changes get evaluated for genericity and propagated here.

**The non-negotiable constraint on every sync.** `factory_core` imports nothing
target-specific. Per-target inputs are **data** loaded through the adapter seams in
`factory_core/adapters.py`. The purity guard (`scripts/check_core_purity.py`, baseline
`core_purity_baseline.json`, token-denylist data file `core_purity_denylist.json`) must
stay **green** after every sync: import scan + token denylist + reverse-dependency assert.
A sync that would require importing target code, naming a target token, or listing a target
dependency is **wrong by construction** and is reformulated as an adapter-seam-driven
generic, or it does not land.

This is an **extraction**, never a copy. The origin target's factory implementations are
target-coupled (they scan that target's routes, name that target's invariants, encode that
target's compliance rules). We extract the *neutral surface* — the IR, the schema, the diff
logic, the discipline — and leave the coupled scanning behind the adapter seams the core
already owns.

---

## Generic advances identified in the origin factory

Three generic advances were identified in the origin target's software-factory work. Each is
genuinely target-agnostic in its design; the origin target merely has the first coupled
implementation.

1. **Change-Surface Audit** — a spec-phase discipline. Every product+tech spec for a
   change enumerates every surface/interaction it touches and classifies each as
   HELD-INVARIANT (with a locking test) or INTENTIONALLY-CHANGED (with a new-contract
   test + proof other consumers are unaffected). 100% process discipline; names no
   target.

2. **Invariant-kernel IR + capability-delta schema + composition-ledger** — a neutral,
   backend-agnostic intermediate representation for platform invariants and the
   per-change capability delta, plus the append-only ledger the analyzer composes
   (shipped + in-flight + candidate) before checking the union against the signed
   kernel. The design is explicitly *not* solver-specific and *not* target-specific:
   role names, data classes, and invariant ids are per-target **data** carried by the
   kernel, not fixed in the core.

3. **Completeness-ledger + reverse-contract (FE↔BE)** — a source-backed inventory that
   makes a completion claim *falsifiable* (enumerate every behavior, entry point, data
   object, residual, and external action), and a pair of contract checks: forward
   (every frontend call targets a real backend endpoint) and reverse (every backend
   operation has — or is explicitly excused from having — a caller). The origin
   implementation scans that target's routes and OpenAPI directly; the **generic** form
   surfaces the route/endpoint inventory through the `RepoAdapter` / `KnowledgeAdapter`
   seams and keeps only the target-agnostic diff logic in the core.

---

## Synced this pass

| Deliverable | File | Why it is target-agnostic |
|---|---|---|
| Change-Surface Audit practice | `docs/practices/change-surface-audit.md` | Pure process documentation. States the spec-phase requirement, the two-bucket classification, the acceptance rule, and a spec template. Names no target; depends on no target code; cannot affect the purity guard (docs are not scanned, and it references only generic factory concepts). |
| Capability-delta JSON Schema | `factory_core/schemas/capability_delta.schema.json` | Pure data (a JSON Schema), sibling to the existing `target_manifest.schema.json`. It is the **neutral IR** for a per-change capability delta: nodes with abstract `roles`, flows with an abstract `data_class` and `relation`, invariants referenced by opaque `id` + `degree` (D0–D4), implementation surfaces, and an optional signature block. Every concrete vocabulary (which roles exist, which data classes, which invariant ids) is per-target data the signed kernel supplies — the schema fixes only the *shape*. It imports nothing; the purity guard scans `.py` files only, and the accompanying test additionally asserts the schema names no configured denylist token. |
| Capability-delta schema tests | `tests/test_capability_delta_schema.py` | Validates the schema is a well-formed draft-2020-12 schema, accepts a minimal and a rich neutral instance, rejects malformed instances (missing `id`, unknown field, bad `degree`, bad `stage`), and asserts the schema file names nothing target-specific. Uses only the allowlisted `jsonschema`. |
| This sync record | `docs/PROVENANCE-SYNC.md` | The standing propagation log + mechanism. Pure documentation. |

**Purity-guard interaction this pass:** none required. The capability-delta schema is a
JSON data file (the guard scans `factory_core/**/*.py`, not `*.json`), it imports
nothing, and it lists no dependency. The baseline (`core_purity_baseline.json`,
`allowed_occurrences: []`) and the denylist (`core_purity_denylist.json`, `tokens: []`)
were **not** touched and did not need to be. `make ship` stays green with no baseline
change.

---

## Synced pass 2 (P1) — invariant-kernel IR + composition-ledger + analyzer

| Deliverable | File | Why it is target-agnostic |
|---|---|---|
| Invariant-kernel module | `factory_core/invariant_kernel.py` | Pure, stdlib + the allowlisted `jsonschema` only, side-effect free at import (matches the `manifest.py` posture). Holds the neutral IR (`GraphNode`, `CapabilityFlow`, `CapabilityDelta`, frozen, parse/`to_dict`), a schema-validated `load_delta`, the **kernel-parameterized** `ReachabilityInvariant` (each invariant supplies its own `start_roles`, `forbidden_terminal_roles`, `degree` as DATA), the append-only `CapabilityLedger` (`append`/`replay` → `ComposedModel`), a backend-neutral `Analyzer` protocol + the built-in `ReachabilityAnalyzer`, the standard `AnalysisResult` (`satisfied`/`violated`/`unsupported`, `Violation` naming invariant id + features + **shortest** path + trace), and the generic delta-fidelity diff over the neutral `SourceFacts` shape. Names **no** target role/invariant/annotation. |
| Golden-counterexample fixtures | `tests/fixtures/invariant_kernel/*.json` | Kernel + deltas over **abstract** roles/ids only (`origin`, `sink`, `store_x`, `hub_z`, `egress_y`, `role_a`). One satisfied case; one composed-violation case (feat-a legal alone, feat-b legal alone, feat-a+feat-b illegal with the shortest path `store_x -> hub_z -> egress_y`). A per-file token test asserts they name nothing target-specific. |
| Invariant-kernel tests | `tests/test_invariant_kernel.py` | schema-load (validated + malformed-rejected), satisfied vs violated, shortest-path correctness (direct edge beats a 2-hop detour), composition (two individually-legal deltas compose to a violation naming both features), cancelled-tombstone + append purity, fail-closed `unsupported` for an undeclared endpoint at a fail-closed degree, delta-fidelity diff, and token checks over the module + fixtures. |

**How the origin coupling was removed (parameterization points):** the origin impl hardcoded
one invariant name (a `no-restricted-store-to-uncontrolled-egress` law), two role constants
(a `restricted_store` role and an `uncontrolled_egress` role), and a target-branded
`<origin>-invariant-*:` annotation prefix. In the core: (1) the invariant name is data — an
opaque `ReachabilityInvariant.id`; (2) the two roles are data — `start_roles` /
`forbidden_terminal_roles` per invariant, read from the kernel; (3) the annotation-scanning
regex is gone entirely — observed-flow **extraction** is impurity that lives behind
`RepoAdapter` (a target adapter returns the neutral `SourceFacts`), so the core keeps only the
generic declared-vs-observed *diff* and scans no target. The information-flow law is now "no
path from any start-role node to any forbidden-terminal-role node," fully parameterized.

**Purity-guard interaction this pass:** none required. `check_core_purity` stays GREEN
(imports allowlisted, no un-baselined token, no reverse dependency) with **no baseline
change** — the module contains zero denylist tokens by construction, and a dedicated test
(`test_module_names_nothing_target_specific` + `test_fixtures_name_nothing_target_specific`)
extends that guarantee to the fixtures the guard does not scan. `make ship`
(purity → lint → typecheck → test) is green; test count 50 → 68.

---

## Synced pass 3 (P2) — completeness-ledger + FE↔BE forward/reverse contract, as adapter-driven generics

| Deliverable | File | Why it is target-agnostic |
|---|---|---|
| Contract module | `factory_core/contract.py` | Pure, stdlib only, side-effect free at import. Holds neutral path/method **normalization** (`{param}` → `{}`, strip scheme+host, strip query/fragment, trim trailing slash — pure string logic, no framework assumption); the neutral inventory records `Endpoint` (a provider operation) and `CallEdge` (a caller edge), each comparable by its normalized `(method, path)` key; a **data-driven** `ExcuseRule` / `ExcuseClassifier` (the rules are input — path prefix/contains/suffix/equals, methods, providers, tags — ANDed per rule, first-match-wins, unclassified ⇒ fail-closed user-facing gap); the **forward** diff (`forward_contract`: resolved caller edges with no provider ⇒ breaks; unresolved edges reported separately); the **reverse** diff (`reverse_contract`: providers with no caller ⇒ orphans, each classified excused-by-design vs. unexcused-gap); and `check_contract` composing both. Names **no** route, service, gateway method, or excuse category. |
| Completeness module | `factory_core/completeness.py` | Pure, stdlib only. Holds the neutral **status lattice** (`GAP < PARTIAL < DECLARED < PROVED`, plus the terminal `EXCUSED` off-ramp; unknown status ⇒ `GAP` fail-closed; `is_complete` = PROVED-or-EXCUSED so a DECLARED *claim* does not close a row; `meet` = least-complete dimension), the neutral `InventoryRow` / `Inventory` shapes with per-status + per-dimension summary counts, and the falsifiable `launch_ready` predicate (green only when every row is PROVED-or-EXCUSED; an empty inventory is **not** vacuously ready unless the caller opts in). Names **no** document, service, or target dimension. |
| Golden fixtures | `tests/fixtures/contract/*.json` | Abstract inventories only (`svc_alpha`, `/api/widgets`, `client_home`, `row-N`, `internal-by-design`) — no target/route/service/compliance vocabulary. A forward break (`POST /api/widgets/{}/publish` with no provider), three reverse orphans classified **both ways** by data rules (two excused: `/internal/…` prefix + a `hook` tag; one unexcused user-facing gap: `/api/orphaned`), and a launch-not-ready vs. ready inventory pair. A per-file token test asserts they name nothing target-specific. |
| Contract-coverage tests | `tests/test_contract_coverage.py` | normalization (params/query/fragment/scheme/trailing-slash); forward diff (break vs. match vs. unresolved); reverse diff with the data-driven classifier (excused both ways, first-match-wins, ANDed predicates, blank-rule-matches-nothing, unclassified-is-a-gap); the lattice (order, DECLARED≠complete, fail-closed unknown, `meet`); launch-readiness (blocking list, ready case, empty-not-vacuously-ready, summary counts); and token checks over both modules + the fixtures. |
| Adapter seam bump | `factory_core/adapters.py` (+ `test_adapters.py`) | See below. |

**Adapter seam bump (the reviewed interface change P2 planned for):** the pure diff/lattice
logic needs its inputs as **neutral inventories**, so `RepoAdapter` gained two typed methods —
`provider_operations() -> Sequence[Endpoint]` and `caller_edges() -> Sequence[CallEdge]` — and
`KnowledgeAdapter` gained `inventory_rows() -> Sequence[InventoryRow]`. These return the neutral
record types from `contract.py` / `completeness.py`; the target-coupled *scanning* (route
modules, provider/OpenAPI specs, source documents) lives entirely inside a target's adapter
implementation, and the core consumes the neutral records and never scans a target. The bump is
minimal and target-agnostic; `test_adapters.py` proves the `ConformingStub` still satisfies all
five seams and that a `RepoAdapter`-shaped class *missing* the new methods is rejected (so the
bump is a real part of the contract, not documentation-only). The seam count stays **five** — no
new seam was introduced; existing seams grew typed methods. The synthetic empty-target
portability proof (`test_portability.py`) is untouched and still green: the core reasons with no
target pack present, and the new seams are Protocol methods a target supplies as data-returning
implementations.

**Purity-guard interaction this pass:** none required — **no baseline change**. Both new
modules contain zero denylist tokens by construction; `contract.py` and `completeness.py` import
only stdlib; `adapters.py` now imports the two neutral record types from the sibling core modules
(intra-`factory_core`, allowlisted). One gotcha surfaced and was fixed without weakening the
guard: the purity guard scans **docstrings** (string-literal AST nodes), so a docstring that
named a target-branded filename could trip a configured token — the seam-bump docstrings refer
to "the sync record under `docs/`" instead of naming a file. `make ship`
(purity → lint → typecheck → test) is green; test count 68 → 93.

---

## Planned — not built this pass (extraction plan)

Per the conservative-sync rule (land the safe high-value pieces; plan the risky ones),
the following remain planned. Each entry states the origin module studied for *shape
only*, the proposed pure `factory_core` surface, the adapter seams it rides, the test
shape, and the purity considerations.

### P1 — Invariant-kernel IR + composition-ledger + analyzer, as a pure core module

> **Status: LANDED** in synced pass 2 (see the table above). The extraction plan below is
> retained as the design record; it was built as specified, with the generalization proven
> by golden-counterexample fixtures over abstract roles rather than asserted.

**Studied for shape only:** the origin target's invariant-kernel module and its design doc.
The IR dataclasses (`KernelNode`, `CapabilityFlow`, `CapabilityDelta`, `CapabilityLedger`,
`ComposedModel`, `AnalysisResult`, `Violation`, `Unsupported`, `FidelityResult`) are
structurally neutral already. The coupling in that file is narrow and enumerable: a hardcoded
invariant name (a `no-restricted-store-to-uncontrolled-egress` law), two hardcoded role names
(a `restricted_store` role and an `uncontrolled_egress` role), and a target-branded
source-annotation prefix (`<origin>-invariant-node:` / `<origin>-invariant-flow:`). Every one
of those is a configured denylist token or a target-specific constant.

**Proposed pure surface:** `factory_core/invariant_kernel.py` — stdlib-only, side-effect
free at import (matching the `manifest.py` posture). It holds:

- the neutral dataclasses above (parse-from-`dict` / `to_dict`, frozen), which the
  capability-delta schema (landed this pass) validates on the way in;
- a `CapabilityLedger.replay(kernel, candidate)` that composes shipped + in-flight +
  candidate into a `ComposedModel`;
- a **backend-neutral analyzer protocol** (like the adapter seams): a backend takes the
  composed model + the kernel's invariant set and returns the standard result schema,
  reporting `unsupported` for any invariant it cannot decide (never turning a
  parser/solver error into a false `legal`);
- **one built-in internal graph/reachability backend** that is written against
  *abstract* roles: it takes the "start role" and the "forbidden-terminal role" as
  **parameters supplied by the kernel data**, not as the origin target's role constants.
  The information-flow law becomes "no path from any node bearing role R_start to any node
  bearing role R_forbidden," where R_start / R_forbidden are kernel-supplied strings. This
  is the exact generalization the origin target's violation check already almost is — it
  just reads its two role names from the kernel instead of module-level constants.
- source-derived delta-fidelity: keep the *diff* logic (declared flows vs. observed
  flows) generic; move the *extraction* of observed flows behind the `RepoAdapter`
  (the origin target's regex over `<origin>-invariant-*:` annotations becomes one adapter
  implementation; the core defines the neutral `SourceFacts` shape it returns).

**Purity considerations:** the module itself must contain **zero** configured denylist tokens.
The law name, the role names, and the annotation prefix are all removed by parametrization. A
dedicated test (mirroring `test_capability_delta_schema`'s token check, plus the purity guard's
own scan) confirms the module is clean. No baseline growth. Risk is moderate (it is real logic,
not just data), which is why it is planned, not built blind — the next pass builds it with
golden-counterexample fixtures (A-legal, B-legal, C-legal, A+B+C-illegal composed from
**abstract** roles) so the generalization is proven, not asserted.

**Test shape:** analyzer-backend conformance (accepts the IR, returns the schema,
reports `unsupported` correctly, never false-legal); golden counterexample fixtures over
abstract roles; composition-ledger tests (cancelled deltas leave tombstones; expired
exceptions stop authorizing); delta-fidelity tests (declared vs. observed). All hermetic,
stdlib-only, no target present.

### P2 — Completeness-ledger + FE↔BE reverse-contract, as adapter-driven generics

> **Status: LANDED** in synced pass 3 (see the table above). The extraction plan below is
> retained as the design record; it was built as specified — the pure diff/normalization/lattice
> logic landed in `factory_core/contract.py` + `factory_core/completeness.py`, and the reviewed
> seam bump added typed inventory methods (`RepoAdapter.provider_operations` / `caller_edges`,
> `KnowledgeAdapter.inventory_rows`) returning the neutral records, with the genericity proven by
> golden fixtures over abstract inventories rather than asserted.

**Studied for shape only:** the origin target's completeness-ledger script and its forward /
reverse FE↔BE contract checkers. These are heavily target-coupled today: they hardcode the
target's doc paths, its per-service OpenAPI layout, its frontend gateway-call idioms, its
gateway method names, and its route-classification rules (which paths are internal, which are
webhooks, which are gateway-owned).

**The generic core inside them is small and clean:**

- **Route/endpoint inventory** is an *input*, not something the core scans. The core
  asks the `RepoAdapter` / `KnowledgeAdapter` for two neutral lists: the set of
  **caller edges** (method, normalized-path-template, source location) and the set of
  **provider operations** (method, normalized-path-template, service, raw path). Path
  normalization (`{param}` → `{}`, strip query, strip trailing slash) is generic and
  belongs in the core.
- **Forward-contract diff:** caller edges whose normalized (method, path) is not in the
  provider set → breaks. Generic set difference.
- **Reverse-contract diff:** provider operations with no caller, minus an
  **excuse-classification** function. The classification rules are the *coupled* part
  (which paths are internal / health / webhook / gateway-owned / runbook). Generalize by
  making the classifier **per-target data**: a list of `{pattern, label, is_excused}`
  rules the adapter supplies; the core applies them. The core owns the diff + the
  "unexcused" residual; the target owns which patterns are excused.
- **Completeness ledger:** the core owns the neutral notion of an *inventory row* with a
  status lattice (e.g. `GAP | PARTIAL | DECLARED | PROVED_AT_REVISION`) and a
  falsifiable "launch-ready" predicate ("every row mapped and proved; no open residual").
  The **parsers** that read the target's behavior catalog / data inventory / open-item
  registry are the coupled part — they become `KnowledgeAdapter` / `ComplianceAdapter`
  reads that return neutral rows. The core owns the aggregation, the summary counts, and
  the launch-readiness predicate; the target owns the source documents and their parsers.

**Proposed pure surface:** `factory_core/contract.py` (path normalization + forward /
reverse diff + data-driven excuse classifier) and `factory_core/completeness.py` (the
neutral inventory-row model + status lattice + launch-readiness predicate + summary).
Both stdlib-only; both take their inputs from the adapter seams, never by scanning a
target tree.

**Adapter-seam additions likely needed (a Phase-boundary decision, human-owned):** the
current `RepoAdapter` exposes `list_files` / `read_file` but not a typed
"caller edges" / "provider operations" inventory, and `ComplianceAdapter` exposes
`invariants` / `impact_preview` but not "inventory rows." Adding neutral methods to the
seams is an interface change to the core's versioned surface, so it is deliberately left
as a **planned, reviewed** step rather than done implicitly in a sync pass. The safe
sequencing is: (a) land the pure diff/normalization logic first with in-memory inputs
and tests (no seam change), (b) then, as a reviewed interface bump, add the typed
inventory methods to the seams.

**Test shape:** forward-contract diff (a caller with no provider is a break; a matching
one is not); reverse-contract diff with a data-driven excuse table (excused operations
do not surface; unexcused ones do); path-normalization unit tests; completeness
launch-readiness predicate (red until all rows mapped+proved; green only when the
residual sets are empty). Hermetic, stdlib-only, no target present.

**Purity considerations:** none of the target's tokens, doc paths, service names, or
gateway idioms enter the core. The classifier patterns and the source parsers stay on
the target side of the adapter seam. A token test guards each new module.

---

## The repeatable mechanism (how future target-factory changes propagate)

This is the standing discipline the founder asked for — run it whenever the origin target's
factory (its factory modules, gates, playbook, invariant/ledger tooling) advances.

1. **Detect.** A change to the origin target's software-factory surface (its factory
   modules, its playbook, its platform-factory design docs, its contract/completeness/ledger
   scripts, or a new numbered discipline) is a candidate for sync. Durable target-side factory
   decisions are captured to the shared knowledge graph; those tagged as factory-process
   advances are the trigger list.

2. **Classify genericity.** For each advance, ask: *is this a property of the software
   factory, or of this particular target?* A discipline (how specs are written, how
   gates compose, how completeness is proven) is almost always generic. A rule
   (this target's invariants, this target's compliance mappings, this target's routes)
   is almost always target data. The test is the purity constraint itself: **if the
   generic form can be expressed with the target's specifics as data through an adapter
   seam, it is generic and belongs here; if it can only be expressed by naming the
   target, it stays in the target.**

3. **Extract the neutral surface.** Study the target implementation for *shape only*.
   Identify the exact coupling (denylist tokens, hardcoded paths, target constants) and
   replace each with a parameter, an adapter-seam call, or per-target data. Never copy
   target code into the core.

4. **Land safe, plan risky.** Pure documentation and self-contained pure data (JSON
   Schemas that import nothing) can land in a single pass. Real logic — especially
   anything that would touch the adapter-seam interfaces or the purity baseline — is
   written up as an extraction plan in this document and built in its own reviewed pass
   with tests first.

5. **Keep `make ship` green.** Every sync pass ends with `make ship`
   (purity → lint → typecheck → test) green. The purity guard is the arbiter: if a sync
   trips it, the sync was not generic enough. Baseline growth is a **reviewable event**
   (a documented exception to the purity guarantee), never a silent escape hatch — and
   the strong default is to reformulate the sync so no baseline entry is needed.

6. **Record here + in the graph.** Update the tables above (synced / planned), and
   capture the sync decision to the shared knowledge graph (`audience=team`) so the log
   travels with the code and future sessions do not re-derive it.

7. **Commit locally; the founder reviews before push.** Sync passes commit in this repo
   and stop. Pushing to the founder's private registry / remote is a separate,
   human-owned step.

---

## Baseline state at first sync

- `factory_core` is at **Phase 0**: skeleton + purity guard. Modules: `manifest.py`
  (content-addressed, hash-chained, SoD-enforcing evidence ledger, stdlib-only),
  `target.py` (data-only `TargetManifest` loader; refuses code references),
  `adapters.py` (five `typing.Protocol` seams: repo, knowledge, compliance, idp,
  artifact_sink), `roles.py` (capability/role schema; grants are per-target data).
  Gates: `make ship` = purity → lint (ruff) → typecheck (mypy) → test (pytest).
- Only third-party runtime dependency: `jsonschema` (allowlisted). Purity baseline has
  **zero** allowed occurrences, and the token denylist ships **empty** (a clean core).
- This is the **first** sync pass. It lands the Change-Surface Audit practice, the
  capability-delta JSON Schema (+ tests), and this record; it plans P1 (invariant-kernel
  IR/analyzer as a pure module) and P2 (completeness-ledger + reverse-contract as
  adapter-driven generics).
