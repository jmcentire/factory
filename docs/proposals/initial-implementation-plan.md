# Factory build plan — revised

> **Status: unratified proposal.** Merging this document would preserve a design for review; it
> would not amend doctrine, perform the genesis ceremony, or authorize implementation.

> **Second revision, against the 2026-08-05 answers on [issue #4](https://github.com/jmcentire/factory/issues/4).**
> Nine items moved from open to decided and are folded in below: the seam count is a design point
> (so the channel-port proposal is un-withdrawn and argued on its merits); slice 5 is next and its
> enforcement point is specified; deliverable 7 comes back into the core; Wander is the target of
> record with reeve and MEA as further consumers; Tessera means real Tessera at pin `83883e62`;
> the three-principal SoD floor is ratified for `enforcing` with n=1 legitimate as the bootstrap
> state; design-in-the-loop is a chosen gap and is now stated as one. Two architectures — **Chess**
> and **Cryptogram** — are adopted by direction rather than proposed here, and get their own
> section. Six governance items remain open and the founder is settling them together.

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

Slices still depend on founder-owned decisions in
[issue #4](https://github.com/jmcentire/factory/issues/4); see *Founder-owned decisions* below,
which now separates the decided from the still-open. A slice marked "not started" whose governing
decision is unanswered is blocked, not merely unscheduled, and the distinction matters for
sequencing.

**Slice 5 is next, by direction.** Not slice 3. The phase loops extend a path that already
functions; slice 5 supplies the transitions that carry authority and currently do not exist
outside a test's bookkeeping.

| Slice | Status | Deliverable | Proof before advancing |
|---|---|---|---|
| **0. Genesis** | machinery delivered; ceremony not performed | CI in the repo; one founder-signed Tessera genesis defining the initial trust root, bootstrap scope, policy digest, and enrolled principals; the interim enrollment registry | Verified against a root fingerprint held outside the candidate branch; an unmapped identity is denied, not defaulted |
| **1. Local gate** | **delivered** | Authorization-request, phase-artifact, receipt, and evidence-bundle schemas; adapter registry; anchor verification; a runnable local gate | A contributor can predict acceptance locally; altered signatures, digests, citations, subjects, or anchor heads fail |
| **2. Supervisor** | **delivered** | `factory_runtime`: persisted run state, transition rules, event ledger, agent executor and sandbox ports | Restarting mid-run resumes from evidence; impossible transitions refuse |
| **3. Three phases** | not started | CLI-first interactive Product Spec, Architecture Spec, and Testing/Monitoring Strategy loops, behind a declared channel port | Each preserves verbatim input, produces behavior-ledger confirmation, and ends in human approval plus an independent Validator attestation anchored to the ledger head |
| **4. Build lanes** | isolation delivered; Pact wiring not started | Separate Coder, Tester, and Validator containers/workspaces; **per-lane cryptogram sections** so the Coder structurally cannot receive test material; **determinism as a lane requirement**; Pact planning wired, implementation lane selected by criticality | Coder cannot read tests *because it holds no key that decrypts them*; Tester cannot read implementation; Validator alone combines and executes; a lane that cannot reproduce a run is refused, not warned; the chosen lane is recorded as evidence |
| **5. Live gate** | **next**; its three states exist but are unenforced — see *The unenforced tail* | Evidence collection, mutation checks, a **real staging surface** a human reviews in, approve / request-changes / abandon **before** merge, a required check plus branch protection as the enforcement point, CI/CD running *after* approval and able to fail independently, promotion of the exact artifact digest, and receipt verification in the core (migrated from reeve `2344645`) | The artifact shown to the human is byte-for-byte the artifact promoted; a merge is actually blocked, not merely advised against; CI failing after an approval blocks promotion rather than being outvoted by it |
| **6. Signet** | not started (scope reduced — see below) | Qualified receipt issuance and verification, key custody, revocation, capability evaluation | Tampered signature, wrong issuer, wrong subject digest, missing capability, expiry, revocation, and replay all deny |
| **7. First live target** | not started; **this ordering is superseded — see below** | A **Wander** target, advisory before blocking; target pack, onboarding path, conformance level | The gate runs green on real traffic for a measured period with an acceptable false-block rate before it blocks anything, *and* the onboarding path is cheap enough that the second Wander target does not repeat the first one's cost |

**Wander is the target of record**, by direction. Reeve and MEA also consume the factory; Wander is
primary. That makes the ordering above wrong rather than merely debatable: as written, every slice
before 7 delivers value only inside this repository, so the actual target is the last thing that
happens. Reeve stays valuable as prior art and as a proving surface, and it is not the destination.
The advisory conformance tier proposed below is how the primary target starts receiving value before
the gate can refuse anything; naming the specific first Wander team is not settled here.

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

**"Enough humans" is a number: three — as the condition for `enforcing`, not as a floor on
legitimacy.** Ratified: `enforcing` requires three distinct enrolled principals for the SoD triad,
so it is unreachable by arithmetic rather than by policy. Explicitly *not* ratified: any reading
where n=1 is illegitimate. One human wearing all three hats is the bootstrap state and is where the
project currently is; three genuinely distinct humans is the `enforcing` state. Both are real.

**The n=1 case and I2 collide in the code, and the collision is unresolved.**
`LedgerEntry.validate_sod` (`factory_core/manifest.py:209`) refuses any two *present-and-equal*
identities unconditionally, under I2 — "no role verifies/approves its own work." So a lone human
who implements and then approves cannot record `human-approved` with the implementer present at
all. Before the anchor controls (PR #9), n=1 reached that state only by leaving
`implementer_identity` empty, which satisfied the SoD check *vacuously* rather than legitimately.
Closing the vacuous pass therefore makes the bootstrap state fail loudly. Three ways out, and the
choice is the founder's: amend I2 for levels below `enforcing`; represent the collapse explicitly
(one identity plus a recorded collapse marker and the conformance level it was recorded under, so
the ledger says what happened); or accept that `human-approved` is unreachable until a second
principal is enrolled. Nothing in `factory_core` or `factory_runtime` currently knows what
`enforcing` *is*, so a conformance level has to exist before option 1 or 2 can be implemented.

**Tessera means real Tessera** — `jmcentire/tessera`, the Rust workspace — and not a lighter
substitute assembled from the convenient parts. It already has the necessary signing and
verification engine and CLI operations (`keygen`, `create`, `sign`, `validate`, `inspect`,
`apply`). Pin `83883e62` is the trust boundary; bumping it is a ratification act, ratified as
stated. Exemplar's `TesseraSeal` stays excluded on capability grounds — it has no signature field —
and the import guard enforcing that stays.

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

**Steps 1–6 are a routing policy, and a routing policy is the wrong kind of control.** Containers
plus a rule about where `decompose` output goes means the firewall holds exactly as long as bundle
assembly is correct. Cryptogram replaces it: the run document carries per-lane sections, each
encrypted for one lane under an ephemeral keypair and bound to the run id so a section cannot be
lifted into another run, and the orchestrator routes envelopes it cannot decrypt. The Coder then
cannot receive test material even when bundle assembly is wrong — a state that cannot occur rather
than a risk that is managed. See *Chess and Cryptogram* below.

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

**Adopted.** SPL is the capability-policy evaluator. It is *not* a third-party evaluator we happen
to be using: `jmcentire/agent-safe` and `signet-eval` are both spin-offs of Signet (signet.tools),
which is the frame to reason in. The split stands — Signet answers *who vouched for this identity*,
SPL answers *what this token is allowed to do* — and neither substitutes for the other.

This is squarely the "capability evaluation" half of slice 6, and it bears directly on
`factory_core/tool_policy.py`, whose Sign-off-required grants are already scoped and expiring, and
on per-lane tool grants.

Two ratified conditions:

- **An SPL expression is an approval rule.** Under I8, no agent may author one into force. An
  expression carried inside a token gets the same human-signature treatment as any other doctrine
  mutation — an agent may draft one and may never put one into effect.
- **Every reference is fully qualified.** `wandercom/agent-safe` is an unrelated Wander repo about
  Pact agent runtime budgets and target-repo adapters. A bare "agent-safe" anywhere in Factory is a
  defect, because someone will wire up the wrong one.

One thing still to settle, and it is ours rather than the founder's: **pinned reference versus
vendored copy.** `factory_core` is stdlib-only plus `jsonschema`, so a dependency needs an allowlist
entry and a justification, while a vendored 150-line total evaluator keeps the dependency surface
honest and forks the source. Whichever holds up better under the purity guard is the one to take;
the earlier revision presented vendoring as settled, and it is not.

Also to verify before either: that `jmcentire/agent-safe` is the origin and not a clone.

### Chess and Cryptogram

Two architectures from the privacy book, adopted by direction rather than proposed here. They are
the answer to provenance and behavior proof, and they replace two things this plan had been solving
adjacently.

**Chess (ch. 13) is the shape the run states should have had.** A document is a genesis block plus
an append-only chain of moves; each move records `prev_state_hash`, the operation, `new_state_hash`,
the actor's public key, and a signature over all four. Verification is five steps: check the actor's
signature; check the declared previous-state hash against the actual previous state; re-execute the
operation in a sandbox against that previous state; check that the independently computed new state
hashes to the declared value; then execute the authorization logic defined in the genesis code to
confirm the actor was permitted the operation. **The validator never trusts a declared outcome — it
recomputes.**

That is precisely the control missing from `human-approved`, `ci`, and `promoted`, which accept a
non-empty actor string. It also relocates chain integrity: it becomes a property of the ledger
rather than of the code that walks it.

- **Step 3 is what turns "the tests passed" into something replayable**, and it comes with an honest
  caveat: re-execution against a hashed prior state buys tamper-evidence unconditionally and
  computational proof only where the run is deterministic. Test execution generally is not — clock,
  network, ordering, parallelism. So the hard part is not the cryptography, it is making lanes
  reproducible, which is why **determinism is a lane requirement in slice 4 and not an aspiration.**
- **Step 5 is SPL, reached from the other direction.** Chess puts authorization logic in the genesis
  code and has the validator execute it; our design puts policy in the token. Genesis already
  carries a policy digest, so signing the transition table itself into genesis is the upgrade
  available to us. The homoiconicity argument in that chapter is also the case for S-expressions:
  code and data hash as one uniform structure, with no format conversion to introduce ambiguity at
  signing or verification time.

**One conflict, settled deliberately rather than by drift.** Chess verifies by replaying the entire
chain from genesis. This plan's evidence rule deliberately does the opposite — sign anchors, chain
machine evidence, each anchor carrying the ledger head digest only, precisely so there are not two
chains. Per-move signing plus full replay will not survive our event volume. **What we take:** the
move-record *shape* for anchor transitions (prior-state hash, operation, new-state hash, actor key,
signature over all four), re-derivation instead of declared outcomes, and replay *between* anchors
with each anchor treated as a checkpoint. **What we do not take:** per-move signing of every ledger
append, and replay from genesis. That is the explicit statement of which parts of Chess are adopted.

**Cryptogram (ch. 11) belongs to the isolated build loop, not here.** A workflow document is
fragmented into sections, each encrypted for one recipient under an ephemeral keypair and bound to
the workflow id so a section cannot be lifted into another workflow, routed by a delegator that
reads the envelope and cannot read the contents. The store cannot see the shipping address because
it cannot decrypt it, not because it promised not to look. Applied to slice 4, it converts "if tests
reach the Coder, oracle independence is theatre" from a managed risk into a state that cannot occur.

### Design in the loop

The factory has no phase for design. That is chosen, not overlooked, and it is stated here so it
does not get discovered later as a defect. The three phases are Product Spec, Architecture Spec, and
Testing/Monitoring Strategy; none of them is a design loop, and nothing in the criticality or
promotion model expects design artifacts. A change that needs design does it outside the factory and
brings the result in as phase-1 input.

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
3. **First live target** — a Wander target, extracting lessons from reeve's existing intake,
   architecture loop, oracle, gate, and demo surfaces without importing reeve code into
   `factory_core`. **Wander is the target of record**; reeve and MEA also consume the factory.
   Reeve's role in this proof is prior art and proving surface — it is the reference implementation
   whose disciplines were generalized into this core — and it is not the destination.

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

## Founder-owned decisions

[Issue #4](https://github.com/jmcentire/factory/issues/4) enumerates governance decisions only the
founder can make. The 2026-08-05 comment answers several; the rest are being settled together
because they fall out of the Chess/Cryptogram framing above. Both halves are recorded here, because
a plan that lists only what is still blocked loses the answers.

**Answered, and folded into this document:**

| Decision | Answer | Landed in |
|---|---|---|
| Is five seams an invariant? | A design point. The declared set moves by explicit decision; the count is not a boundary condition | PR #10 (`adapters.py` docstring plus a declared-set guard); both claimants argued on merit |
| Which slice is next | Slice 5, not slice 3 | Build order, slice 5 marked **next** |
| What the enforcement point is | A required check plus branch protection and a real staging surface; human approves / requests changes / abandons **before** merge; CI/CD runs after and can fail independently. CI promotion is not a gate | Slice 5 deliverable and proof columns |
| Deliverable 7's side of the boundary | Core. Target-side means every target reimplements a control | Slice 5; migration from reeve `2344645` |
| Target of record | Wander, primary. Reeve and MEA also consume; reeve is prior art and a proving surface | Slice 7 note; proof 3 |
| Tessera | Real `jmcentire/tessera`, the Rust workspace. Pin `83883e62` is the trust boundary and bumping it is a ratification act. `TesseraSeal` stays banned; the guard stays | *The bootstrap* |
| The three-human floor | Ratified for `enforcing`; n=1 is the legitimate bootstrap state and is where we are | *The bootstrap*, with the I2 collision stated |
| Design in the loop | A chosen gap, to be stated rather than discovered | *Design in the loop* |
| SPL | Adopted; a Signet spin-off, not a third-party evaluator. Two conditions: I8 governs authoring an expression into force, and every reference is fully qualified | *Policy evaluation* |

**Still open, and being settled together:**

| Decision | Asked in | Gates |
|---|---|---|
| Where authorization lives | issue #4 BQ1 | any change entering through the intake |
| Proposal/ship capability vocabulary and receipt issuer | issue #4 BQ2 | slices 0, 5, 6 |
| Who may hold proposal vs. ship authority | issue #4 BQ3, BQ6 | slice 0 |
| How enrollment reaches the core, now that the seam count is a design point | issue #4 BQ4 | slice 0 |
| Whether a reduced phase form exists | issue #4 BQ5 | slice 3; the cost of every small change |
| How forked contributors consume the authorization record | issue #4 BQ7 | any external contribution |

One consequence of BQ5 staying open is worth stating rather than discovering: check 8 of the
authorization-request validator (`phase_form_granted`) fails closed, so the validator ships with a
branch that permanently refuses `reduced` until the answer arrives. That is the correct failure
direction and it is an interim state, not the design.

### The sixth seam: resolved, and both claimants live

**Five was a design decision, not a boundary condition.** So the count moves by explicit decision
rather than by whichever proposal merges first, and the guard now enforces the *declared set* rather
than the cardinality: `adapters.py` amended and `test_there_are_exactly_five_seams` replaced by a
declared-set guard in PR #10, which grants no seam to either claimant.

Both claimants — BQ4's enrollment/authority reach, and this plan's channel port — are therefore
decided on their own merits against the declared set. Neither answers the other by landing first.
The channel port is **un-withdrawn** as a live proposal on that basis; the reasoning it was
withdrawn for ("the slot is not free") no longer holds.

### Deliverable 7 comes back across the seam

Receipt verification against payload hash and required capabilities belongs in the core. It is
implemented on *reeve* main at `2344645`, and target-side is the wrong shape: every target would
reimplement a control. **Plan the migration from reeve `2344645` into Factory** — it is now a slice-5
deliverable rather than an open question. Verified absent today: no `payload_digest` and no receipt
verification anywhere in `factory_core/*.py`.

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
6. **Wander is the target of record**; reeve and MEA also consume the factory, and the gate blocks
   nothing until its false-block rate is measured. The slice-7 ordering is superseded: it put the
   primary target last. See *An advisory conformance tier below slice 7* in the proposed table for
   how the primary target starts receiving value earlier.
7. Capability *policy* is evaluated by SPL (`jmcentire/agent-safe`) carried inside the token —
   a Signet spin-off, distinct from Signet, which answers identity and vouching. An SPL expression
   is an approval rule: under I8 no agent may author one into force. Pinned reference versus
   vendored copy is unsettled; see *Policy evaluation*.
8. **Chess supplies the shape of an anchor transition and Cryptogram supplies the lane firewall.**
   Adopted by direction, with the parts taken and not taken stated explicitly in
   *Chess and Cryptogram*. Determinism becomes a lane requirement in slice 4 as a consequence.
9. **The factory has no design phase**, by choice. Stated so it is not later found as a defect.

---

## Appendix — what changed from your draft, and why

Settled per your direction:

| Change | Basis |
|---|---|
| Tessera anchors rather than envelopes everything; the evidence rule; the anchor constraint | Your ratification of option C |
| Tessera identified as `jmcentire/tessera`, with exemplar's `TesseraSeal` excluded by capability | Your correction; verified — the exemplar model has no signature field |
| Pact: planning primary, implementation opt-in for sensitive work | Your answer on cost and placement |
| The seam count is a design point; the guard enforces the declared set | Your 2026-08-05 answer. Both sixth-seam claimants are argued on merit against that set |
| Slice 5 is next, and its enforcement point is a required check plus branch protection plus a real staging surface, with approval before merge and CI after | Your 2026-08-05 answer. "CI promotion" standing in for a gate is explicitly rejected |
| Deliverable 7 — receipt verification — comes back into the core, migrated from reeve `2344645` | Your 2026-08-05 answer: target-side means every target reimplements a control |
| Wander is the target of record; reeve and MEA also consume | Your 2026-08-05 answer. The build order had the primary target last |
| Real Tessera (`jmcentire/tessera`), pin `83883e62` as a ratification act | Your 2026-08-05 answer, ratified as stated. A lighter substitute was an artifact of drafting, not a decision |
| Three distinct enrolled principals at `enforcing`; n=1 legitimate as the bootstrap state | Your 2026-08-05 answer. The arithmetic was right; the floor is not a minimum on legitimacy |
| SPL adopted, as a Signet spin-off, under two conditions (I8 authorship, fully-qualified references) | Your 2026-08-05 answer |
| Chess and Cryptogram adopted, with the parts taken stated explicitly | Your 2026-08-05 answer, including the instruction to state which parts of Chess we take |
| Design in the loop recorded as a chosen gap | Your 2026-08-05 answer |

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
| Reeve repositioned from target of record to prior art and proving surface; Wander named as the target of record | Confirmed by the founder on 2026-08-05. The build order had encoded the opposite, which pushed the primary target behind all seven slices. The previous revision over-corrected by calling reeve "explicitly not what this factory is being built to serve" — reeve and MEA are consumers too; Wander is primary. |
| The channel-port withdrawal itself reversed | Withdrawing it was right while the slot looked contested and wrong once the count turned out to be a design point. Recorded rather than silently un-withdrawn, because the reasoning for the withdrawal is what changed, not the proposal. |
| Interpreter detected and floor enforced in the `Makefile` (applied, not proposed) | `make ship` resolved `PY` to bare `python3` and `lint`/`typecheck` bypassed `PY` entirely. `PY` now prefers `python3.12` and falls back to `python3`, `check-python` gates every target, and the tools run through `$(PY) -m`. Verified across five paths: preferred version found; activated venv preferred over system; fallback to a conforming `python3`; refusal of 3.9.20 with a "not on PATH" hint; and explicit `PY=` still overriding detection. |

Proposed for your ratification — reject individually:

| Proposed | Why |
|---|---|
| Interim enrollment registry in slice 0; unmapped identity denied | Genesis enrolls principals, but with Signet deferred nothing says where the Google/GitHub/Slack/Linear mapping lives. Whoever can edit it defeats SoD, so it is Critical by §3.5's own enumeration. |
| Pinned reference or vendored copy for SPL | SPL itself is ratified; this half was left to us. Vendoring a ~150-line total evaluator keeps `factory_core` stdlib-only and forks the source; a pin needs an allowlist entry and a justification but tracks upstream fixes. Recommending vendoring, and not treating that as settled. |
| "Delivered against the synthetic target" stated explicitly per slice | Otherwise the status column reads as "proven," and proof 1 below silently loses its purpose. |
| Local environment is a repo-managed venv, and the sandbox derives its interpreter grant (applied, not proposed) | Slice 1's proof is that a contributor can predict acceptance *locally*, and `make ship` had never been green on a developer machine: the interpreter floor was unenforced, and the one gate that did run refused any venv. Both are now fixed, and `make ship` is green locally end to end (330 passed, 3 skipped) as well as under a simulated CI interpreter. The sandbox change is the one to review deliberately — it widens a `deny default` profile — but it restores a precondition the hardcoded allowlist was already trying to express, and the denial probes still pass. |
| Key custody tiers in the bootstrap | A signature is worth its custody. Root offline, anchor keys human-held, no agent holds a key at any tier. |
| Channel port declared before slice 3 — **un-withdrawn** | The three phases are inherently conversational and CLI + Slack + portal are one seam with N renderers, better decided before three renderers exist. It was withdrawn on the grounds that the slot was not free; the seam count is a design point, so that reasoning is gone. It is back as an ordinary proposal, argued against the declared set on its own merits, and it does not answer BQ4's enrollment reach by landing. |
| An advisory conformance tier below slice 7 | **The ordering change itself is directed, not proposed** — the primary target cannot be the last thing that happens. This is the cheapest mechanism for it. Several `factory_core` modules — `contract.py`, `completeness.py`, `comprehensiveness.py`, and `criticality.py`'s classification model — are pure functions over data arriving through read-only seams and produce *documents*, not enforcement. They need no genesis, enrollment, Signet, promotion, isolation, or Pact. A read-only advisory tier lets a Wander team get findings long before the gate can refuse anything, and tests the disciplines against a real team before machinery is built to enforce them. Not free: the target-side extraction that feeds `caller_edges` and `provider_operations` is real work the core deliberately does not own. Explicitly *not* proposed for `monitors.py` or `promotion.py`, which require phase artifacts and oracle adequacy respectively. |
| Replace proof 2 with a new small change rather than retiring it | The adapter-registry content already landed on `main` at `cba4f7f`, so reprocessing it cannot fail in the way that matters. A first-authorized-change proof needs a change that has not already been merged. |
| Slice 7 names a specific first Wander team | Wander as target of record is settled; *which* team goes first is not, and this document should not decide it. Naming a first-of-many puts the weight on the onboarding path either way, since a per-target cost that does not amortize fails at target three regardless of how well target one goes. |
| False-block rate as the slice-7 gate | A governance gate that wrongly blocks even a few percent of changes gets switched off by the organization, permanently. |
| Pact test output firewalled from the Coder | `decompose` emits contracts and tests together; contracts are shared, tests must not be. A five-line policy now, an expensive silent failure later. |
| Three named reeve lessons in proof 3 | "Extract lessons" is unactionable; these three are specific, cited, and preserve the import boundary. |
