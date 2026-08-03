# Factory build plan — revised

> **Status: unratified proposal.** Merging this document would preserve a design for review; it
> would not amend doctrine, perform the genesis ceremony, or authorize implementation.

We build one real vertical slice, with Factory as the authoritative supervisor. We do not begin by
wiring every tool together or declaring Signet mandatory.

The executable shape is:

```text
Human ↔ surfaces (CLI first; Slack, portal later — capability parity, one API)
             │
     Factory runtime/state machine
             │
             ├── factory_core: provenance, checklists, criticality, promotion
             ├── Coder sandbox
             ├── Tester sandbox
             └── Validator sandbox
                      │
        ┌─────────────┴──────────────┐
        │ evidence plane (two parts) │
        │  · factory_core ledger —   │  the chain + write-time SoD policy
        │    every event, machine    │
        │    speed, no keys          │
        │  · Tessera — Ed25519       │  signed anchors for human authority acts
        │    signed anchor documents │  and independent Validator attestations;
        │                             │  each carries the LEDGER HEAD DIGEST only
        └─────────────┬──────────────┘
                      │
              Artifact storage + CI/CD
```

Pact may provide low-level agent invocation, but Factory must control every spawn and transition.
Handing the entire run to `pact run` would make Pact — not Factory — the operational state machine.

Two corrections to my earlier shorthand:

- **Tessera is the signing and anchoring authority, not a wrapper around every event.** The
  `factory_core` ledger remains the chain and the write-time policy gate. `ArtifactSink` stores
  Tessera bytes.
- Signet is a future control-plane `AuthorityProvider`; `IdpAdapter` remains target-user
  authentication. Those are different jobs.

## The evidence rule

**Sign human authority acts and independent Validator attestations. Chain machine evidence.
Anchor the chain at each signed checkpoint.**

Neither component can do the other's job, and this is why:

| | `factory_core/manifest.py` | Tessera |
|---|---|---|
| Imports | `hashlib`, `hmac`, `json` — **no key material, no signatures** | Ed25519 (`tessera/crates/tessera-core/src/crypto.rs`), per-mutation + whole-document |
| Answers | *was this allowed to be written* — SoD refusal, deny-wins identity, enrolled-human approver | *who vouched for this* — authenticated authorship, multi-actor, replay from genesis |
| Cannot | root a chain in a trust authority; a self-derived chain has no root | express segregation of duties at all |

The integration socket already exists in the code: `SegregationPolicy.require_signature` refuses an
append unless `implementer_provenance.signature_verified` is true. manifest.py never verifies a
signature — it records that one was verified elsewhere and fails closed without it. Tessera is that
elsewhere.

**What is signed** (low frequency, human-mediated, key ceremony fits):

- the genesis document, the enrolled-principal set, the policy digest
- each phase artifact, with a human approval receipt and a distinct Validator attestation receipt
- each approval and risk acceptance
- the promoted artifact digest

**What is chained, not signed** (high frequency, machine-generated, independently re-verified by CI
per slice 5): test results, mutation results, gate outcomes, per-environment results, transition
events.

**The anchor constraint.** A Tessera anchor document carries the **ledger head digest**, never a
copy of ledger entries. The moment it carries entries there are two chains, and "which is
authoritative when they disagree" becomes a question someone answers wrong under pressure.

**Which Tessera.** `github.com/jmcentire/tessera` — the Rust workspace. **Not** exemplar's
`TesseraSeal` (`exemplar/src/schemas/schemas.py:332`), which is a five-field Pydantic model with
`content_hash` / `previous_hash` / `chain_hash` / `sealed_at` / `sealer_id` and **no signature field
of any kind**. It cannot verify a founder signature against a fingerprint, so it cannot perform
slice 0 at all. This is a capability gap, not a preference — enforce it with an import guard in the
runtime repo.

## Build order

Status is a proposal snapshot reviewed against the PR's current base, not a promise that later
changes to `main` are already reflected here. This revision is verified against `94e7bb1`, where
`make ship` is green (330 passed, 3 skipped) and `make test-tessera` is green against the real
signing binary.

Six slices additionally depend on founder-owned decisions that are open in
[issue #4](https://github.com/jmcentire/factory/issues/4); see *Founder-owned decisions this plan
is blocked on* below. A slice marked "not started" whose governing decision is unanswered is
blocked, not merely unscheduled, and the distinction matters for sequencing.

| Slice | Status | Deliverable | Proof before advancing |
|---|---|---|---|
| **0. Genesis** | machinery delivered; ceremony not performed | CI in the repo; one founder-signed Tessera genesis defining the initial trust root, bootstrap scope, policy digest, and enrolled principals; the interim enrollment registry | Verified against a root fingerprint held outside the candidate branch; an unmapped identity is denied, not defaulted |
| **1. Local gate** | **delivered** | Authorization-request, phase-artifact, receipt, and evidence-bundle schemas; adapter registry; anchor verification; a runnable local gate | A contributor can predict acceptance locally; altered signatures, digests, citations, subjects, or anchor heads fail |
| **2. Supervisor** | **delivered** | `factory_runtime`: persisted run state, transition rules, event ledger, agent executor and sandbox ports | Restarting mid-run resumes from evidence; impossible transitions refuse |
| **3. Three phases** | not started | CLI-first interactive Product Spec, Architecture Spec, and Testing/Monitoring Strategy loops, behind a declared channel port | Each preserves verbatim input, produces behavior-ledger confirmation, and ends in human approval plus an independent Validator attestation anchored to the ledger head |
| **4. Build lanes** | isolation delivered; Pact wiring not started | Separate Coder, Tester, and Validator containers/workspaces; Pact planning wired, implementation lane selected by criticality | Coder cannot read tests; Tester cannot read implementation; Validator alone combines and executes; the chosen lane is recorded as evidence |
| **5. Live gate** | not started; its three states exist but are unenforced — see *The unenforced tail* | Evidence collection, mutation checks, ephemeral preview, human approval, CI promotion of the exact artifact digest, and an enforcement point that can refuse a merge | The artifact shown to the human is byte-for-byte the artifact promoted; a merge is actually blocked, not merely advised against |
| **6. Signet** | not started (scope reduced — see below) | Qualified receipt issuance and verification, key custody, revocation, capability evaluation | Tampered signature, wrong issuer, wrong subject digest, missing capability, expiry, revocation, and replay all deny |
| **7. First live target** | not started | The Wander **`sync` team** as the first of numerous Wander targets, advisory before blocking; target pack, onboarding path, conformance level | The gate runs green on real traffic for a measured period with an acceptable false-block rate before it blocks anything, *and* the onboarding path is cheap enough that the second Wander target does not repeat the first one's cost |

### What slices 0–2 already are

Naming the shipped surfaces matters, because the next slices must extend them rather than
re-invent them under the plan's provisional names.

- **CI** is `.github/workflows/ci.yml` (landed `42b63e4`, 2026-07-28). A `verify` job runs
  `make ship` on Python 3.12 and then the real-Tessera integration test; a second `macos-14`
  job runs `make test-isolation` and `make test-tessera`. It checks out `jmcentire/tessera`
  pinned to `83883e62` — the pin is the trust boundary, so bumping it is a ratification act,
  not a chore.
- **The local gate is `make ship`**, fail-closed in order: purity → doctrine → lint → typecheck
  → test. There is no `factory gate` or `make gate`; earlier drafts of this plan named commands
  that do not exist. The interpreter is now detected and guarded: the bootstrap interpreter prefers `python3.12`
  when it is on `PATH` and falls back to `python3` otherwise; locally, `PY` then defaults to the repo-managed `.venv/bin/python`
  (unless `CI` is set or `PY` is given explicitly), and `check-python` — a prerequisite of every gate — refuses anything below the `requires-python` floor, reporting
  `check_python: GREEN — …` / `RED — …` in the same shape as `check_core_purity` and
  `check_doctrine_sync`, rather than letting a pre-3.12 `python3` reach `check_core_purity.py`
  and die on `import tomllib`. Because detection goes through `PATH`, an activated venv wins over a system
  install, which is the intent: prefer a conforming interpreter, do not escape the environment
  the contributor chose. `PY` is authoritative for the tools too — `ruff` and `mypy` run as
  `$(PY) -m`, so a gate cannot silently lint under a different interpreter than it tests under.
  `make show-python` reports what detection resolved to.
- **Local runs use a repo-managed virtualenv.** `make <anything>` creates `.venv` on first use,
  keeps it in sync whenever `pyproject.toml` is newer than its stamp, and runs every gate out of
  it — no activation step, and no way for two contributors to be testing against different
  dependency sets. Management is skipped when `CI` is set or when `PY` was given explicitly, so
  the runner keeps provisioning its own interpreter and the workflow needed no change.
- **The isolation sandbox now derives the interpreter grant instead of hardcoding it.**
  `isolation.py`'s Seatbelt profile is `deny default`, and it allowlisted `/opt/homebrew`,
  `/usr/local`, `/bin`, and `/private/etc` — an approximation of "wherever the interpreter
  lives" that held only for a Homebrew interpreter. Every sandboxed command is `sys.executable`,
  so under a venv the child died in `init_import_site` reading `pyvenv.cfg` before any lane code
  ran. The grant is now derived from `sys.prefix`, `sys.base_prefix`, and the resolved
  executable's directory. This is a precondition of isolation, not a relaxation of it: a sandbox
  that cannot read the interpreter is not stricter, only unusable. The denial probes — forbidden
  read, forbidden write, bind, connect — still pass unchanged, which is what makes that claim
  checkable rather than rhetorical.
- **Genesis is machinery, not yet a ceremony.** `genesis.schema.json`, `verify-genesis`, the
  roster projection in `authority.py:54`, and real Tessera signing under
  `test_tessera_cli_integration.py` all exist. What does *not* exist is a founder-signed genesis
  artifact produced with an offline key, or the enrollment registry behind it. Slice 0 closes
  when the ceremony is performed, not when the verifier compiles.
- **The runtime CLI verbs** are `validate-document`, `digest-json`, `status`,
  `rebuild-projection`, `verify-genesis`, `authorize-change`, `ratify-phase`, and
  `tessera-wrap`. Slice 3 adds phase-loop verbs alongside these.
- **The schemas** are `factory_runtime/schemas/{genesis,authorization-request,authority-receipt,phase-artifact,evidence-bundle}.schema.json`.
- **The supervisor** is `factory_runtime/` (~3.3k lines): `state.py` holds `RunState` and
  `ALLOWED_TRANSITIONS`; `orchestrator`, `isolation`, `lanes`, `evidence_plane`, `authority`,
  `tessera`, `workflow`, and `cli` are the rest. The test suite covers it, including
  `test_isolated_build_loop.py`, `test_tessera_cli_integration.py`, and `test_runtime_state.py`.

**Caveat on "delivered."** These slices are delivered against the synthetic target only. Their
proof columns are asserted by the suite, not yet by a real run — which is what proof 1 below is
for. Delivered means the surface exists and its tests pass; it does not mean the slice has been
exercised end to end on real work.

### The unenforced tail of the happy path

Proof 1 has now been run as far as it goes, and this is its result. It is recorded here rather
than only in the proofs section because it changes a status column.

`test_real_runtime_reaches_preview_through_authority_isolation_tests_and_evidence` walks
`intake` → `preview` with real authority, real isolation, and real evidence, and asserts a
terminal `RunState.PREVIEW` (`tests/test_tessera_cli_integration.py:463`). The signing is real:
the test *skips* rather than fails when `FACTORY_TESSERA_BIN` is unset, and the configured
binary is present. **Past `preview` there is no driver at all:**

- `HUMAN_APPROVED`, `CI`, and `PROMOTED` appear nowhere in `factory_runtime` outside the
  transition table in `state.py`. Zero production callers.
- The only code that walks them is `tests/test_runtime_state.py:66`, which hand-cranks
  `store.transition(...)` through the state names with `actor="validator"` — including
  `human-approved`.
- `RunStore.transition` requires an artifact digest only for the three `*-ratified` states
  (`_PHASE_STATE_KEYS`, `factory_runtime/state.py:117`). For `human-approved`, `ci`, and
  `promoted` it requires a non-empty `actor` string and nothing else: no receipt, no signature,
  no distinct approver, and `approver_identity` defaults to `""`.

The consequence for sequencing is that the happy path is six-ninths real, and the missing third
is precisely the part that carries authority. **Slice 5, not slice 3, is where the next
load-bearing code work is** — slice 3's phase loops extend a path that already functions, while
slice 5 supplies transitions that currently do not exist outside a test's bookkeeping.

This is not an argument that the `SegregationPolicy` is absent. It is applied at ledger append
(`state.py:426`), and a stricter configured policy may well refuse an empty-identity promote.
What is verified is that nothing on the promote path *supplies* identities, so the default
configuration never exercises it. Whether a stricter policy would in fact refuse is untested and
is a slice-5 obligation to demonstrate rather than assume.

### The run state

`RunState` in `factory_runtime/state.py` is the authority. The states are:

```text
intake
  → product-specification-ratified
  → architecture-ratified
  → operational-maturity-ratified
  → building
  → validating
  → preview
  → human-approved
  → ci
  → promoted
```

plus two states off the happy path that the code already models and this plan previously
described only in prose:

```text
specification-defect    a frozen spec awaiting a newly signed version
blocked                 a run that cannot legally proceed
```

Each `*-ratified` transition requires a human approval receipt and a distinct enrolled non-human
Validator receipt over the exact artifact bytes. This is enforced: those three states are the
entries in `_PHASE_STATE_KEYS` and a missing artifact digest refuses the transition.

`human-approved` and `promoted` are *doctrinally* signed anchor points and are **not yet enforced
as such**. The earlier revision of this document stated the requirement without marking it
aspirational, which read as a description of the code. As of `94e7bb1` those two transitions
require a non-empty `actor` string and nothing further; see *The unenforced tail of the happy
path* above. Making them anchor points in fact is a slice-5 deliverable.

The other transitions are chained events. `specification-defect` is itself an anchor point: it
freezes the current version, creates a newly signed one, and invalidates every downstream
artifact derived from the old digest — also doctrine ahead of code, since it has no production
driver and is reached only by test bookkeeping (`tests/test_runtime_state.py:172`).

`blocked` is the exception among the off-happy-path states and should not be lumped in with the
others: it is genuinely driven, from four `store.transition` call sites in
`factory_runtime/orchestrator.py` (a fifth `RunState.BLOCKED` reference, at line 182, is the
membership test that lets a blocked attempt resume, not a driver), and a real
failing build is asserted to reach it (`tests/test_tessera_cli_integration.py:453`).

### The bootstrap

The first gate cannot arrive through itself. That is not a Critical waiver; it is the root-of-trust
ceremony that creates the system.

Jeremy signs a narrowly scoped genesis document with an offline key. The trusted public-key
fingerprint is read from protected CI configuration or an installed local trust store — never from
the PR branch being judged. The private key is unavailable to agents.

Genesis authorizes only construction and activation of the gate. Once enough humans are enrolled and
CI plus denial probes pass, the system irreversibly switches from `bootstrap` to `enforcing`.

**Key custody follows the SoD model, or the signatures are theatre.** The root key is offline and
human-only. Human authority keys are held by enrolled humans and exercised for approval, never by
a build lane. A Validator may hold a separate enrolled non-human attestation key, restricted to
verification capabilities; it is not a human approval key and must be distinct from the
implementer and approver identities. Coder and Tester identities receive no human authority key.
A signature is worth exactly what its key policy authorizes, which is the second reason to sign
few things.

**"Enough humans" is a number: three.** Below three enrolled principals the SoD triad — implementer
≠ verifier ≠ approver — is unsatisfiable, so `enforcing` is unreachable by arithmetic rather than by
policy.

Actual Tessera already has the necessary signing and verification engine and CLI operations
(`keygen`, `create`, `sign`, `validate`, `inspect`, `apply`).

### The isolated loop

For each run:

1. Factory gives Coder and Tester identical signed phase-artifact bytes.
2. Coder receives source but no test workspace.
3. Tester receives the interface/schema surface but no implementation workspace.
4. Both run with separate filesystems, identities, tool grants, and no shared Kindex/thread.
5. Validator receives both outputs in a third clean environment and runs tests.
6. A failure returns only `pass` or `fail`; no assertion, test name, or trace reaches the Coder.
7. Validator owns mutation evidence and assembles checklist evidence incrementally.
8. A passing artifact is deployed into a fresh preview for human acceptance.
9. CI independently re-verifies everything and promotes that exact digest.

`signet-eval` can enforce declarative tool policy inside each lane, but only as defense in depth. Its
own documentation correctly says same-UID agents can bypass it; real separation requires containers
or OS controls.

### Pact's placement

**Planning on every change; implementation opt-in.** Pact's `PLANNING_PHASES` are already a named
constant (`pact/src/pact/scheduler.py:61` — `interview`, `shape`, `decompose`, `diagnose`), and
v1's default `pact run` stops before implementing. Factory drives the planning phases and never
passes `--implement` except where the lane policy says so.

- **Lane selection is data, not judgment.** Criticality is already a property of the surface,
  assigned by a human at design formalization and inherited by every change that disturbs it.
  Critical surfaces take the Pact implementation lane; Standard and Cosmetic take the cheap lane.
  "Use it judiciously" is the rule that erodes under deadline; a control-profile lookup does not.
- **The gate is invariant across lanes.** Same frozen oracle, same adequacy measure, same human
  floor. Lane choice changes production cost, never how a change is judged — which is what keeps
  blast-radius gating from re-entering through the implementer.
- **The lane is recorded in the manifest**, so "Pact is worth 10× on Critical surfaces" becomes a
  measurable Epistemic-tier claim (correction rate and denial rate per lane) instead of folklore.
- **Test material is firewalled from the Coder.** `decompose` emits contracts *and* tests —
  Pact's own comment says so (`pact/src/pact/scheduler.py:65`: "artifacts (contracts, tests)
  first appear in decompose"). Contracts are legitimately shared — the spec is shared. Pact's test output routes
  to the Tester/oracle side only. If it reaches the Coder inside the shared bundle, oracle
  independence is theatre.

### Why Signet comes later

Verified at signet `7f71a87`. This section previously claimed two defects; one has been fixed
upstream and the other is in a different crate than stated. The corrected picture makes slice 6
**smaller**, not larger.

**`signet-cred` is a real credential engine.** As of `7f71a87` (2026-06-20, "Harden capability
verification and fail-close issuance", #5), `signet/crates/signet-cred/src/capability.rs` genuinely
verifies: `verify_capability_for_context` constructs an `Ed25519CapabilityVerifier`, calls
`verify_strict` over `header || payload`, then runs `validate_capability_time_window`
(iat/nbf/exp consistency, not merely expiry) and `validate_capability_context`.
`signet/crates/signet-cred/src/authority.rs` does real sign/verify for authority offers, multi-authority
delegation chains, and user acceptances, with tests for tampering, wrong keys, broken chain links,
and expired offers. Nothing here needs building; it needs *qualifying*.

**The defect is one function in a different crate.** `signet/crates/signet-sdk/src/authority.rs`
`check_authority` computes a SHA-256 binding over `(signet_id, authority)` and then discards it:

```rust
fn is_authority_granted(binding: &[u8; 32]) -> bool {
    binding.iter().any(|&b| b != 0)
}
```

That is unconditionally true for any structurally valid SignetId and any of the seven
`KNOWN_AUTHORITIES`. Its own comment block contradicts itself — "roughly 50% of valid SignetIds"
in one paragraph, "we always grant" in the next — and its tests assert the blanket grant. This is
worse than an honest stub: it hashes first, so it *looks* like a decision at the call site. The
control here is a ban, not a rewrite: **`signet-sdk::check_authority` must never appear on a
Critical path**, enforced by an import guard in the runtime repo in the same way exemplar's
`TesseraSeal` is excluded.

**One real gap, and it is on the slice-6 proof list.** `one_time` capabilities are refused
outright at *both* issuance and acceptance, pending a consumption ledger. So the "replay" denial
in slice 6's proof column has nothing to enforce it yet for one-time capabilities. That ledger is
a slice-6 deliverable, not an upstream assumption.

Revised slice-6 scope, therefore: qualify `signet-cred`'s existing verification against our own
denial probes; supply the consumption ledger that unblocks one-time capabilities; add key custody
and revocation (no revocation path exists in signet today); and guard the SDK authority seam out
of the Critical path. Signet is still not required to build the first honest Factory — and
anchoring rather than per-event signing keeps the surface Signet must eventually cover small: a
handful of authority acts, not every ledger append.

### Policy evaluation: `agent-safe` / SPL

`jmcentire/agent-safe` defines **SPL (Safe Policy Lisp)** — a total, deterministic, gas-metered
S-expression policy language that travels *inside* a signed capability token, so the verifier
decides locally (~15µs) with no policy server in the request path. It offers token sealing to stop
further attenuation down a delegation chain, set membership and comparison predicates, crypto
predicates, and hash-chain offline budgets, in ~150 lines per evaluator across six languages
including Python.

This is squarely the "capability evaluation" half of slice 6, and it bears directly on
`factory_core/tool_policy.py`, whose Sign-off-required grants are already scoped and expiring, and
on per-lane tool grants. The two fit together cleanly: Signet answers *who vouched for this
identity*, SPL answers *what this token is allowed to do*, and neither substitutes for the other.

Two things to settle before adopting it (see ratification item 7):

- **Purity.** `factory_core` is stdlib-only plus `jsonschema`. An SPL evaluator is either a new
  runtime dependency requiring an allowlist entry and a justification, or a vendored single file.
  Vendoring a 150-line total-evaluation function is the cheaper of the two and keeps the
  dependency surface honest.
- **Name collision.** `wandercom/agent-safe` is an unrelated Wander repo about Pact agent runtime
  budgets and target-repo adapters. Any reference to "agent-safe" in Factory must be
  fully qualified, or someone will wire up the wrong one.

### First three proofs

1. **Run** — attempted, and it terminates at `preview`. The flow is exercised end to end from
   `intake` to `preview` against the synthetic target with real signing; the three states past
   `preview` have no driver. Result recorded under *The unenforced tail of the happy path* above.
   The remainder of this proof is blocked on slice 5, not on scheduling.
2. **Reauthorize** — the premise has expired. This proof was "reauthorize and rebuild Jon's
   adapter-registry change from PR #2 through the new intake," but that content is already on
   `main`: `factory_core/registry.py` was added by `cba4f7f`, and the
   `glue/adapter-registry-readonly-git` branch is now a stale duplicate of it (`f0a72f6`, same
   subject, different SHA). The change that was nominated as the first authorized change landed
   by the ordinary route instead. Two honest options: retire this proof, or replace it with a
   *new* small change chosen to be the first thing through the intake. The second is worth more,
   because a proof that reprocesses already-merged content cannot fail in the way that matters.
3. **First live target** — the Wander `sync` team, extracting lessons from reeve's existing
   intake, architecture loop, oracle, gate, and demo surfaces without importing reeve code into
   `factory_core`. Reeve is **prior art, not a target of record**: it is the reference
   implementation whose disciplines were generalized into this core, and it is explicitly not
   what this factory is being built to serve.

Three specific lessons to extract from reeve for proof 3, all preserving the import boundary:

- **Demo provisioning discipline** — operation-manifest claim for idempotency; quarantine on crash
  rather than auto-retry, because minting an environment is not idempotent and retry orphans it; a
  TTL reaper that guarantees no preview outlives its window.
- **The seam slice 5 must close** — `reeve/src/factory/merge-executor.ts:27` documents it exactly:
  "the actual git merge + entry into the CI/CD pipeline is a RECORDED NO-OP here." The decision
  path is real; the execution is not. That is precisely the gap.
- **Spec-defect handling already implemented** — `reeve/src/factory/spec-version.ts` and
  `reeve/src/factory/oracle-freeze.ts` implement freeze / re-sign /
  invalidate-downstream-by-digest, with the oracle's `specDigest` re-derived
  (`oracle-freeze.ts:91`) so the binding cannot be forged.

## Founder-owned decisions this plan is blocked on

[Issue #4](https://github.com/jmcentire/factory/issues/4) enumerates governance decisions that
only the founder can make, and its Aug-1 comment states plainly that they remain open even though
the issue's original evidence table is now historical. Several of them gate slices in the table
above, so they belong in this document rather than only in the issue. Deduplicated against this
plan's own ratification list, so each is answered once:

| Decision | Asked in | Gates |
|---|---|---|
| Where authorization lives | issue #4 BQ1 | any change entering through the intake |
| Proposal/ship capability vocabulary and receipt issuer | issue #4 BQ2 | slices 0, 5, 6 |
| Who may hold proposal vs. ship authority | issue #4 BQ3, BQ6 | slice 0; the SoD floor |
| How enrollment reaches the core without breaching the seam | issue #4 BQ4 **+ this plan's channel port** | slices 0, 3 — **and colliding, see below** |
| Whether a reduced phase form exists | issue #4 BQ5 | slice 3; the cost of every small change |
| How forked contributors consume the authorization record | issue #4 BQ7 | any external contribution |

### The sixth seam has two claimants

This plan proposes a channel port "declared before slice 3." Issue #4 BQ4 independently asks
whether Signet reaches the core through `IdpAdapter`, a sixth seam by explicit redesign, or from
outside the core entirely. **These are two proposals for the same slot, in two documents, neither
citing the other.**

The slot is not free, and neither proposal is additive. `factory_core/adapters.py:10` states "The
seams are the whole target surface. There is deliberately no sixth: anything a target needs the
factory to do must fit one of these, or the boundary has been breached," and
`tests/test_adapters.py:73::test_there_are_exactly_five_seams` enforces it. Either proposal must
therefore amend a stated invariant *and* delete a guard test, and whichever lands first silently
answers BQ4 on behalf of the other.

The decision to make is upstream of both: **is five an invariant, or a design point?** This plan
takes no position on the answer and withdraws any claim to the slot until that is settled.

### One deliverable is on the wrong side of the boundary

Issue #4's deliverable 7 — receipt verification against payload hash plus required capabilities —
is implemented, but per the issue's Aug-2 comment it is implemented on *reeve* main at `2344645`,
not in Factory. Since reeve is prior art rather than a target of record, a control living only
there means every future Wander target reimplements it. Whether it crosses back into the core is
an open question this plan cannot answer for the founder, and it is not currently on any slice.

## Architecture decisions needing ratification

1. Factory owns the runtime state machine.
2. Pact is a planning engine on every change and an opt-in implementation lane selected by
   criticality; never the state machine.
3. **Tessera signs and anchors human authority acts; the `factory_core` ledger chains machine
   evidence and enforces write-time SoD; an anchor carries the ledger head digest only.**
4. Signet's credential engine (`signet-cred`) is qualified rather than rebuilt;
   `signet-sdk::check_authority` is import-guarded off every Critical path; the consumption ledger
   that unblocks one-time capabilities is ours to build.
5. Surfaces are renderers over one API at capability parity, behind a declared channel port.
6. The first live target is the Wander `sync` team — the first of numerous Wander targets — and
   the gate blocks nothing until its false-block rate is measured. Reeve is prior art, not a
   deployment target. **This ordering is the one worth arguing about:** as written, every slice
   before 7 delivers value only inside this repository, which puts the actual target last. See
   *An advisory conformance tier below slice 7* in the proposed table.
7. Capability *policy* is evaluated by SPL (`jmcentire/agent-safe`) carried inside the token,
   vendored as a single file rather than added as a runtime dependency — distinct from Signet,
   which answers identity and vouching.

---

## Appendix — what changed from your draft, and why

Settled per your direction:

| Change | Basis |
|---|---|
| Tessera anchors rather than envelopes everything; the evidence rule; the anchor constraint | Your ratification of option C |
| Tessera identified as `jmcentire/tessera`, with exemplar's `TesseraSeal` excluded by capability | Your correction; verified — the exemplar model has no signature field |
| Pact: planning primary, implementation opt-in for sensitive work | Your answer on cost and placement |

Corrected in this revision — these were errors in the previous version of this document, not
proposals:

| Correction | What was wrong |
|---|---|
| Slices 1–2 marked delivered, slice 0 split into machinery vs. ceremony; the shipped surfaces named | The draft's basis was `d652982`; eleven code commits landed before this document was committed. Presenting shipped work as pending is the one error that misdirects effort — and slice 0 is the inverse trap, since its verifier exists but the founder-signed genesis does not. |
| CI is a fact, not a proposal | The appendix previously proposed CI as a slice-0 deliverable on the basis that "the repo has no `.github/` and no CI today." `.github/workflows/ci.yml` landed at `42b63e4` on 2026-07-28 — already false when written. |
| `make ship`, not `factory gate` / `make gate` | The named commands never existed. The plan cannot ask a contributor to run a command that is not there. |
| Real `RunState` names, plus `specification-defect` and `blocked` | The plan said `phase-1/2/3-ratified`; the code says `product-specification-ratified`, `architecture-ratified`, `operational-maturity-ratified`, and models two states the plan omitted. |
| "Why Signet comes later" rewritten | `signet-cred`'s capability verification was fixed upstream at `7f71a87`; the surviving defect is `signet-sdk::check_authority`, and it grants unconditionally rather than "structurally pending another layer." The plan was both stale and too charitable. |
| `crates/` prefix restored on signet and tessera paths | The cited paths did not resolve as written. |
| External citations qualified with their repository (`pact/…`, `reeve/…`, `signet/…`, `tessera/…`) | The cited paths did not resolve as written, and a bare `scheduler.py:61` reads as a file in *this* repo when it is Pact's. A citation a reader cannot locate is not evidence. |
| `human-approved` and `promoted` marked as doctrine ahead of code | The plan stated "`human-approved` and `promoted` are also signed anchor points" as flat fact. Verified at `94e7bb1`: those two states have zero production callers, `RunStore.transition` requires only a non-empty `actor` for them, and `approver_identity` defaults to `""`. Stating an unenforced requirement in the same voice as an enforced one is the error that makes a status column unreliable — and it hid the fact that slice 5, not slice 3, is the next load-bearing work. |
| `blocked` distinguished from the other off-happy-path states | Grouping it with `specification-defect` understated it. `blocked` is genuinely driven from four `store.transition` call sites in `orchestrator.py` and a real failing build reaches it (`test_tessera_cli_integration.py:453`); `specification-defect` has no production driver at all. |
| Proof 1 recorded as attempted with a result, not pending | It has been run as far as the code allows. Leaving it listed as future work would have lost its finding, which is the whole reason the proof exists. |
| Proof 2's premise expired | `factory_core/registry.py` is on `main` at `cba4f7f`; `glue/adapter-registry-readonly-git` is a stale duplicate. The plan asked to rebuild through the intake a change that had already landed by the ordinary route. |
| Reeve demoted from target of record to prior art; Wander `sync` named as first target | Stated by the founder-adjacent scope owner during this revision. The build order had encoded the opposite, which pushed the actual target behind all seven slices. |
| The channel-port proposal withdrawn rather than restated | It claimed a slot that issue #4 BQ4 is actively deliberating, and presented as additive a change that must amend `adapters.py:10` and delete a guard test. |
| Interpreter detected and floor enforced in the `Makefile` (applied, not proposed) | `make ship` resolved `PY` to bare `python3` and `lint`/`typecheck` bypassed `PY` entirely. `PY` now prefers `python3.12` and falls back to `python3`, `check-python` gates every target, and the tools run through `$(PY) -m`. Verified across five paths: preferred version found; activated venv preferred over system; fallback to a conforming `python3`; refusal of 3.9.20 with a "not on PATH" hint; and explicit `PY=` still overriding detection. |

Proposed for your ratification — reject individually:

| Proposed | Why |
|---|---|
| Interim enrollment registry in slice 0; unmapped identity denied | Genesis enrolls principals, but with Signet deferred nothing says where the Google/GitHub/Slack/Linear mapping lives. Whoever can edit it defeats SoD, so it is Critical by §3.5's own enumeration. |
| SPL (`jmcentire/agent-safe`) as the capability-policy evaluator, vendored not depended on | Slice 6 lists "capability evaluation" with nothing behind it. SPL is a total, gas-metered evaluator that travels in the token, and `tool_policy.py` already has the scoped-and-expiring grant shape it evaluates. Vendoring keeps `factory_core` stdlib-only. |
| The CI Tessera pin (`83883e62`) treated as a ratification act | The pin *is* the verifier trust boundary. If bumping it is a routine chore, the trust root moves without a decision. |
| "Delivered against the synthetic target" stated explicitly per slice | Otherwise the status column reads as "proven," and proof 1 below silently loses its purpose. |
| Local environment is a repo-managed venv, and the sandbox derives its interpreter grant (applied, not proposed) | Slice 1's proof is that a contributor can predict acceptance *locally*, and `make ship` had never been green on a developer machine: the interpreter floor was unenforced, and the one gate that did run refused any venv. Both are now fixed, and `make ship` is green locally end to end (330 passed, 3 skipped) as well as under a simulated CI interpreter. The sandbox change is the one to review deliberately — it widens a `deny default` profile — but it restores a precondition the hardcoded allowlist was already trying to express, and the denial probes still pass. |
| "Enough humans" fixed at three | The SoD triad is unsatisfiable below three; `enforcing` should be unreachable by arithmetic, not by judgment. |
| Key custody tiers in the bootstrap | A signature is worth its custody. Root offline, anchor keys human-held, no agent holds a key at any tier. |
| An enforcement point in slice 5 | Nothing in the draft can refuse a merge. "CI promotion" is not a gate; a required check with branch protection is. |
| ~~Channel port declared before slice 3~~ — **withdrawn pending the seam-count decision** | The reasoning still holds: the three phases are inherently conversational and CLI + Slack + portal are one seam with N renderers, better decided before three renderers exist. What was wrong was proposing it as though the slot were free. It collides with issue #4 BQ4 and cannot be granted without amending `adapters.py:10` and deleting `test_adapters.py:73`. Resubmit once "is five an invariant?" is answered. |
| An advisory conformance tier below slice 7 | As written, no slice delivers anything outside this repository until the last one, which puts the actual target last. Several `factory_core` modules — `contract.py`, `completeness.py`, `comprehensiveness.py`, and `criticality.py`'s classification model — are pure functions over data arriving through read-only seams and produce *documents*, not enforcement. They need no genesis, enrollment, Signet, promotion, isolation, or Pact. A read-only advisory tier would let the Wander `sync` team get findings long before the gate can refuse anything, and would test the disciplines against a real team before machinery is built to enforce them. Not free: the target-side extraction that feeds `caller_edges` and `provider_operations` is real work the core deliberately does not own. Explicitly *not* proposed for `monitors.py` or `promotion.py`, which require phase artifacts and oracle adequacy respectively. |
| Replace proof 2 with a new small change rather than retiring it | The adapter-registry content already landed on `main` at `cba4f7f`, so reprocessing it cannot fail in the way that matters. A first-authorized-change proof needs a change that has not already been merged. |
| Slice 7 names the Wander `sync` team as the first of numerous Wander targets | The draft treated reeve as the target of record and Wander deployment as implied follow-on scope. It is the reverse: reeve is prior art, and Wander is what this is for. Naming `sync` as *first of many* also puts weight on the onboarding path, since a per-target cost that does not amortize fails at target three regardless of how well target one goes. |
| False-block rate as the slice-7 gate | A governance gate that wrongly blocks even a few percent of changes gets switched off by the organization, permanently. |
| Pact test output firewalled from the Coder | `decompose` emits contracts and tests together; contracts are shared, tests must not be. A five-line policy now, an expensive silent failure later. |
| Three named reeve lessons in proof 3 | "Extract lessons" is unactionable; these three are specific, cited, and preserve the import boundary. |
