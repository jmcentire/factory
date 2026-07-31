# Factory build plan — revised

> Revision of `jeremys_plan.md`. Two decisions are now settled (evidence architecture; Pact
> placement) and are written in as settled. Everything else marked in the appendix is a proposed
> delta for your ratification, not a change made on your behalf.

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
| Imports | `hashlib`, `hmac`, `json` — **no key material, no signatures** | Ed25519 (`tessera-core/src/crypto.rs`), per-mutation + whole-document |
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

| Slice | Deliverable | Proof before advancing |
|---|---|---|
| **0. Genesis** | CI in the repo; one founder-signed Tessera genesis defining the initial trust root, bootstrap scope, policy digest, and enrolled principals; the interim enrollment registry | Verified against a root fingerprint held outside the candidate branch; an unmapped identity is denied, not defaulted |
| **1. Local gate** | Authorization-request, phase-artifact, receipt, and evidence-bundle schemas; adapter registry; anchor verification; `factory gate` and `make gate` | A contributor can predict acceptance locally; altered signatures, digests, citations, subjects, or anchor heads fail |
| **2. Supervisor** | `factory_runtime`: persisted run state, transition rules, event ledger, agent executor and sandbox ports | Restarting mid-run resumes from evidence; impossible transitions refuse |
| **3. Three phases** | CLI-first interactive Product Spec, Architecture Spec, and Testing/Monitoring Strategy loops, behind a declared channel port | Each preserves verbatim input, produces behavior-ledger confirmation, and ends in human+Validator signatures anchored to the ledger head |
| **4. Build lanes** | Separate Coder, Tester, and Validator containers/workspaces; Pact planning wired, implementation lane selected by criticality | Coder cannot read tests; Tester cannot read implementation; Validator alone combines and executes; the chosen lane is recorded as evidence |
| **5. Live gate** | Evidence collection, mutation checks, ephemeral preview, human approval, CI promotion of the exact artifact digest, and an enforcement point that can refuse a merge | The artifact shown to the human is byte-for-byte the artifact promoted; a merge is actually blocked, not merely advised against |
| **6. Signet** | Qualified receipt issuance and verification, key custody, revocation, capability evaluation | Tampered signature, wrong issuer, wrong subject digest, missing capability, expiry, revocation, and replay all deny |
| **7. First live external target** | One target beyond reeve, advisory before blocking; target pack, onboarding path, conformance level | The gate runs green on real traffic for a measured period with an acceptable false-block rate before it blocks anything |

The run state should be explicit:

```text
intake
  → phase-1-ratified
  → phase-2-ratified
  → phase-3-ratified
  → building
  → validating
  → preview
  → human-approved
  → CI
  → promoted
```

Each `*-ratified`, `human-approved`, and `promoted` transition is a **signed anchor point**. The
others are chained events.

A specification defect freezes the current version, creates a newly signed version, and invalidates
every downstream artifact derived from the old digest.

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
- **Test material is firewalled from the Coder.** `decompose` emits contracts *and* tests. Contracts
  are legitimately shared — the spec is shared. Pact's test output routes to the Tester/oracle side
  only. If it reaches the Coder inside the shared bundle, oracle independence is theatre.

### Why Signet comes later

Current Signet cannot yet be the mandatory Critical authority:

- Its capability parser separates the signature but does not verify it (`signet-cred/src/capability.rs:142`).
- Its SDK authority check explicitly grants known authorities structurally while deferring real
  authorization to another layer (`signet-sdk/src/authority.rs:74`).

We harden and qualify that path before replacing the bootstrap authority. Signet is not required to
build the first honest Factory — and anchoring rather than per-event signing keeps the surface Signet
must eventually cover small: a handful of authority acts, not every ledger append.

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
4. Signet joins only after its authority verifier is genuinely enforcing.
5. Surfaces are renderers over one API at capability parity, behind a declared channel port.
6. The system is not deployed beyond reeve until slice 7, and blocks nothing until its false-block
   rate is measured.

---

## Appendix — what changed from your draft, and why

Settled per your direction:

| Change | Basis |
|---|---|
| Tessera anchors rather than envelopes everything; the evidence rule; the anchor constraint | Your ratification of option C |
| Tessera identified as `jmcentire/tessera`, with exemplar's `TesseraSeal` excluded by capability | Your correction; verified — the exemplar model has no signature field |
| Pact: planning primary, implementation opt-in for sensitive work | Your answer on cost and placement |

Proposed for your ratification — reject individually:

| Proposed | Why |
|---|---|
| CI added as a slice-0 deliverable | Slice 0 reads the genesis fingerprint from "protected CI configuration," but the repo has no `.github/` and no CI today. It is a prerequisite hiding inside the slice. |
| Interim enrollment registry in slice 0; unmapped identity denied | Genesis enrolls principals, but with Signet deferred nothing says where the Google/GitHub/Slack/Linear mapping lives. Whoever can edit it defeats SoD, so it is Critical by §3.5's own enumeration. |
| "Enough humans" fixed at three | The SoD triad is unsatisfiable below three; `enforcing` should be unreachable by arithmetic, not by judgment. |
| Key custody tiers in the bootstrap | A signature is worth its custody. Root offline, anchor keys human-held, no agent holds a key at any tier. |
| An enforcement point in slice 5 | Nothing in the draft can refuse a merge. "CI promotion" is not a gate; a required check with branch protection is. |
| Channel port declared before slice 3 | `factory_core` has five seams and no channel seam, yet the three phases are inherently conversational and you want Slack + portal + CLI at parity. That is one seam with N renderers — better decided before three renderers exist. |
| Slice 7, and the statement that deployment starts after it | No target beyond reeve appears in the draft. Wander deployment is a real scope and should be visible as one, not implied. |
| False-block rate as the slice-7 gate | A governance gate that wrongly blocks even a few percent of changes gets switched off by the organization, permanently. |
| Pact test output firewalled from the Coder | `decompose` emits contracts and tests together; contracts are shared, tests must not be. A five-line policy now, an expensive silent failure later. |
| Three named reeve lessons in proof 3 | "Extract lessons" is unactionable; these three are specific, cited, and preserve the import boundary. |
