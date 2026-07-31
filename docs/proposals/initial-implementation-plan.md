We build one real vertical slice, with Factory as the authoritative supervisor. We do not begin by wiring every tool together or declaring Signet mandatory.

The executable shape is:

```text
Human ↔ Factory runtime/state machine
             │
             ├── factory_core: provenance, checklists, criticality, promotion
             ├── Coder sandbox
             ├── Tester sandbox
             └── Validator sandbox
                      │
              Tessera evidence envelope
                      │
              Artifact storage + CI/CD
```

Pact may provide low-level agent invocation, but Factory must control every spawn and transition. Handing the entire run to `pact run` would make Pact—not Factory—the operational state machine.

One correction to my earlier shorthand:

- Tessera is the integrity envelope; `ArtifactSink` merely stores its bytes.
- Signet is a future control-plane `AuthorityProvider`; `IdpAdapter` remains target-user authentication. Those are different jobs.

## Build order

| Slice | Deliverable | Proof before advancing |
|---|---|---|
| **0. Genesis** | One founder-signed Tessera genesis defining the initial trust root, bootstrap scope, policy digest, and enrolled principals | Verified against a root fingerprint held outside the candidate branch |
| **1. Local gate** | Authorization-request, phase-artifact, receipt, and evidence-bundle schemas; adapter registry; `factory gate` and `make gate` | A contributor can predict acceptance locally; altered signatures, digests, citations, or subjects fail |
| **2. Supervisor** | `factory_runtime`: persisted run state, transition rules, event ledger, agent executor and sandbox ports | Restarting mid-run resumes from evidence; impossible transitions refuse |
| **3. Three phases** | CLI-first interactive Product Spec, Architecture Spec, and Testing/Monitoring Strategy loops | Each preserves verbatim input, produces behavior-ledger confirmation, and ends in human+Validator signatures |
| **4. Build lanes** | Separate Coder, Tester, and Validator containers/workspaces | Coder cannot read tests; Tester cannot read implementation; Validator alone combines and executes |
| **5. Live gate** | Evidence collection, mutation checks, ephemeral preview, human approval, CI promotion of the exact artifact digest | The artifact shown to the human is byte-for-byte the artifact promoted |
| **6. Signet** | Qualified receipt issuance and verification, key custody, revocation, capability evaluation | Tampered signature, wrong issuer, wrong subject digest, missing capability, expiry, revocation, and replay all deny |

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

A specification defect freezes the current version, creates a newly signed version, and invalidates every downstream artifact derived from the old digest.

### The bootstrap

The first gate cannot arrive through itself. That is not a Critical waiver; it is the root-of-trust ceremony that creates the system.

Jeremy signs a narrowly scoped genesis document with an offline key. The trusted public-key fingerprint is read from protected CI configuration or an installed local trust store—never from the PR branch being judged. The private key is unavailable to agents.

Genesis authorizes only construction and activation of the gate. Once enough humans are enrolled and CI plus denial probes pass, the system irreversibly switches from `bootstrap` to `enforcing`.

Actual Tessera already has the necessary signing and verification engine and CLI operations ([CLI](/Users/jmcentire/Code/tessera/crates/tessera/src/main.rs:15), [Ed25519 implementation](/Users/jmcentire/Code/tessera/crates/tessera-core/src/crypto.rs:20)).

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

`signet-eval` can enforce declarative tool policy inside each lane, but only as defense in depth. Its own documentation correctly says same-UID agents can bypass it; real separation requires containers or OS controls ([limitations](/Users/jmcentire/Code/signet-eval/README.md:350)).

### Why Signet comes later

Current Signet cannot yet be the mandatory Critical authority:

- Its capability parser separates the signature but does not verify it ([capability.rs](/Users/jmcentire/Code/signet/crates/signet-cred/src/capability.rs:142)).
- Its SDK authority check explicitly grants known authorities structurally while deferring real authorization to another layer ([authority.rs](/Users/jmcentire/Code/signet/crates/signet-sdk/src/authority.rs:74)).

We harden and qualify that path before replacing the bootstrap authority. Signet is not required to build the first honest Factory.

### First three proofs

1. Run the complete flow against the synthetic target already in Factory.
2. Reauthorize and rebuild Jon's adapter-registry change from PR #2 through the new intake.
3. Make Reeve the first live target, extracting lessons from its existing intake, architecture loop, oracle, gate, and demo surfaces without importing Reeve code into `factory_core`.

The four architecture decisions needing your ratification are: Factory owns the runtime state machine; Pact is only a leaf executor; Tessera is the initial evidence envelope; and Signet joins only after its authority verifier is genuinely enforcing.
