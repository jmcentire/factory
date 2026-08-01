# Factory build plan — revised

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
        │  · Tessera — Ed25519       │  signed anchors at human authority acts;
        │    signed anchor documents │  each carries the LEDGER HEAD DIGEST only
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

**Sign human authority acts. Chain machine evidence. Anchor the chain at each authority act.**

Neither component can do the other's job, and this is why:

| | `factory_core/manifest.py` | Tessera |
|---|---|---|
| Imports | `hashlib`, `hmac`, `json` — **no key material, no signatures** | Ed25519 (`crates/tessera-core/src/crypto.rs`), per-mutation + whole-document |
| Answers | *was this allowed to be written* — SoD refusal, deny-wins identity, enrolled-human approver | *who vouched for this* — authenticated authorship, multi-actor, replay from genesis |
| Cannot | root a chain in a trust authority; a self-derived chain has no root | express segregation of duties at all |

The integration socket already exists in the code: `SegregationPolicy.require_signature` refuses an
append unless `implementer_provenance.signature_verified` is true. manifest.py never verifies a
signature — it records that one was verified elsewhere and fails closed without it. Tessera is that
elsewhere.

**What is signed** (low frequency, human-mediated, key ceremony fits):

- the genesis document, the enrolled-principal set, the policy digest
- each phase-artifact ratification (human + Validator)
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

Status is a statement about the tree at `97e44c3`, not about intent.

| Slice | Status | Deliverable | Proof before advancing |
|---|---|---|---|
| **0. Genesis** | machinery delivered; ceremony not performed | CI in the repo; one founder-signed Tessera genesis defining the initial trust root, bootstrap scope, policy digest, and enrolled principals; the interim enrollment registry | Verified against a root fingerprint held outside the candidate branch; an unmapped identity is denied, not defaulted |
| **1. Local gate** | **delivered** | Authorization-request, phase-artifact, receipt, and evidence-bundle schemas; adapter registry; anchor verification; a runnable local gate | A contributor can predict acceptance locally; altered signatures, digests, citations, subjects, or anchor heads fail |
| **2. Supervisor** | **delivered** | `factory_runtime`: persisted run state, transition rules, event ledger, agent executor and sandbox ports | Restarting mid-run resumes from evidence; impossible transitions refuse |
| **3. Three phases** | not started | CLI-first interactive Product Spec, Architecture Spec, and Testing/Monitoring Strategy loops, behind a declared channel port | Each preserves verbatim input, produces behavior-ledger confirmation, and ends in human+Validator signatures anchored to the ledger head |
| **4. Build lanes** | isolation delivered; Pact wiring not started | Separate Coder, Tester, and Validator containers/workspaces; Pact planning wired, implementation lane selected by criticality | Coder cannot read tests; Tester cannot read implementation; Validator alone combines and executes; the chosen lane is recorded as evidence |
| **5. Live gate** | not started | Evidence collection, mutation checks, ephemeral preview, human approval, CI promotion of the exact artifact digest, and an enforcement point that can refuse a merge | The artifact shown to the human is byte-for-byte the artifact promoted; a merge is actually blocked, not merely advised against |
| **6. Signet** | not started (scope reduced — see below) | Qualified receipt issuance and verification, key custody, revocation, capability evaluation | Tampered signature, wrong issuer, wrong subject digest, missing capability, expiry, revocation, and replay all deny |
| **7. First live external target** | not started | One target beyond reeve, advisory before blocking; target pack, onboarding path, conformance level | The gate runs green on real traffic for a measured period with an acceptable false-block rate before it blocks anything |

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
  that do not exist. One footgun: the `Makefile` invokes bare `python3`, so on a machine whose
  `python3` predates 3.11 the very first gate dies on `import tomllib` in
  `scripts/check_core_purity.py`. CI pins 3.12; local contributors need a venv. "A contributor
  can predict acceptance locally" is slice 1's proof, so this is in scope, not cosmetic.
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
  `tessera`, `workflow`, and `cli` are the rest. Thirty-four test modules cover it, including
  `test_isolated_build_loop.py`, `test_tessera_cli_integration.py`, and `test_runtime_state.py`.

**Caveat on "delivered."** These slices are delivered against the synthetic target only. Their
proof columns are asserted by the suite, not yet by a real run — which is what proof 1 below is
for. Delivered means the surface exists and its tests pass; it does not mean the slice has been
exercised end to end on real work.

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

Each `*-ratified`, `human-approved`, and `promoted` transition is a **signed anchor point**. The
others are chained events. `specification-defect` is itself an anchor point: it freezes the
current version, creates a newly signed one, and invalidates every downstream artifact derived
from the old digest.

### The bootstrap

The first gate cannot arrive through itself. That is not a Critical waiver; it is the root-of-trust
ceremony that creates the system.

Jeremy signs a narrowly scoped genesis document with an offline key. The trusted public-key
fingerprint is read from protected CI configuration or an installed local trust store — never from
the PR branch being judged. The private key is unavailable to agents.

Genesis authorizes only construction and activation of the gate. Once enough humans are enrolled and
CI plus denial probes pass, the system irreversibly switches from `bootstrap` to `enforcing`.

**Key custody follows the SoD model, or the signatures are theatre.** Three tiers: the root key is
offline and human-only; anchor-signing keys are held by enrolled humans and exercised at
ratification, never by a lane; no agent identity holds a signing key at any tier. A signature is
worth exactly what its custody is worth, which is the second reason to sign few things.

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
constant (`scheduler.py:61` — `interview`, `shape`, `decompose`, `diagnose`), and v1's default `pact
run` stops before implementing. Factory drives the planning phases and never passes `--implement`
except where the lane policy says so.

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
  Pact's own comment says so (`scheduler.py:65`: "artifacts (contracts, tests) first appear in
  decompose"). Contracts are legitimately shared — the spec is shared. Pact's test output routes
  to the Tester/oracle side only. If it reaches the Coder inside the shared bundle, oracle
  independence is theatre.

### Why Signet comes later

Verified at signet `7f71a87`. This section previously claimed two defects; one has been fixed
upstream and the other is in a different crate than stated. The corrected picture makes slice 6
**smaller**, not larger.

**`signet-cred` is a real credential engine.** As of `7f71a87` (2026-06-20, "Harden capability
verification and fail-close issuance", #5), `crates/signet-cred/src/capability.rs` genuinely
verifies: `verify_capability_for_context` constructs an `Ed25519CapabilityVerifier`, calls
`verify_strict` over `header || payload`, then runs `validate_capability_time_window`
(iat/nbf/exp consistency, not merely expiry) and `validate_capability_context`.
`crates/signet-cred/src/authority.rs` does real sign/verify for authority offers, multi-authority
delegation chains, and user acceptances, with tests for tampering, wrong keys, broken chain links,
and expired offers. Nothing here needs building; it needs *qualifying*.

**The defect is one function in a different crate.** `crates/signet-sdk/src/authority.rs`
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

1. Run the complete flow against the synthetic target already in Factory.
2. Reauthorize and rebuild Jon's adapter-registry change from PR #2 through the new intake.
3. Make Reeve the first live target, extracting lessons from its existing intake, architecture loop,
   oracle, gate, and demo surfaces without importing Reeve code into `factory_core`.

Three specific lessons to extract in proof 3, all preserving the import boundary:

- **Demo provisioning discipline** — operation-manifest claim for idempotency; quarantine on crash
  rather than auto-retry, because minting an environment is not idempotent and retry orphans it; a
  TTL reaper that guarantees no preview outlives its window.
- **The seam slice 5 must close** — reeve's `merge-executor.ts` documents it exactly: "the actual git
  merge + entry into the CI/CD pipeline is a RECORDED NO-OP here." The decision path is real; the
  execution is not. That is precisely the gap.
- **Spec-defect handling already implemented** — reeve's `spec-version.ts` and `oracle-freeze.ts`
  implement freeze / re-sign / invalidate-downstream-by-digest, with the oracle's `specDigest`
  re-derived so the binding cannot be forged.

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
6. The system is not deployed beyond reeve until slice 7, and blocks nothing until its false-block
   rate is measured.
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

Proposed for your ratification — reject individually:

| Proposed | Why |
|---|---|
| Interim enrollment registry in slice 0; unmapped identity denied | Genesis enrolls principals, but with Signet deferred nothing says where the Google/GitHub/Slack/Linear mapping lives. Whoever can edit it defeats SoD, so it is Critical by §3.5's own enumeration. |
| SPL (`jmcentire/agent-safe`) as the capability-policy evaluator, vendored not depended on | Slice 6 lists "capability evaluation" with nothing behind it. SPL is a total, gas-metered evaluator that travels in the token, and `tool_policy.py` already has the scoped-and-expiring grant shape it evaluates. Vendoring keeps `factory_core` stdlib-only. |
| The CI Tessera pin (`83883e62`) treated as a ratification act | The pin *is* the verifier trust boundary. If bumping it is a routine chore, the trust root moves without a decision. |
| "Delivered against the synthetic target" stated explicitly per slice | Otherwise the status column reads as "proven," and proof 1 below silently loses its purpose. |
| Pin the interpreter in the `Makefile` as a slice-1 fix | `make ship` calls bare `python3`, so on a pre-3.11 `python3` the first gate dies on `import tomllib`. Slice 1's proof is that a contributor can predict acceptance *locally*; a gate that only runs in CI does not satisfy it. |
| "Enough humans" fixed at three | The SoD triad is unsatisfiable below three; `enforcing` should be unreachable by arithmetic, not by judgment. |
| Key custody tiers in the bootstrap | A signature is worth its custody. Root offline, anchor keys human-held, no agent holds a key at any tier. |
| An enforcement point in slice 5 | Nothing in the draft can refuse a merge. "CI promotion" is not a gate; a required check with branch protection is. |
| Channel port declared before slice 3 | `factory_core` has five seams and no channel seam, yet the three phases are inherently conversational and you want Slack + portal + CLI at parity. That is one seam with N renderers — better decided before three renderers exist. |
| Slice 7, and the statement that deployment starts after it | No target beyond reeve appears in the draft. Wander deployment is a real scope and should be visible as one, not implied. |
| False-block rate as the slice-7 gate | A governance gate that wrongly blocks even a few percent of changes gets switched off by the organization, permanently. |
| Pact test output firewalled from the Coder | `decompose` emits contracts and tests together; contracts are shared, tests must not be. A five-line policy now, an expensive silent failure later. |
| Three named reeve lessons in proof 3 | "Extract lessons" is unactionable; these three are specific, cited, and preserve the import boundary. |
