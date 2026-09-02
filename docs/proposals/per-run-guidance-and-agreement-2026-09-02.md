# Per-run guidance and cross-path agreement controls

## 0. Stakes

**Expensive-to-revert, assessed separately for two publications.** Both implementations are
additive and can be reverted in code, but each changes a versioned public Factory contract. A bad
shape would require coordinated migration of run authors and retained artifacts; it does not
transform or destroy production data. The agreement contract ships first as one release. The
guidance contract ships second as another release and may share utilities, but neither schema,
gate, digest chain, nor migration depends on the other.

## 1. Problem statement

Factory currently asks the Validator to research standards and to drive the result end to end,
but a run has no closed, per-run way for its user to select additional standards, process loops,
or implementation recipes and then prove that each was applied in the right place. Prompting an
agent to “also follow this document” loses provenance, applicability, role separation, and
completion evidence. Separately, `res-r1` demonstrated that “end-to-end” can be satisfied by
testing two paths independently: quote and hold each had tests, yet nothing asserted their shared
decision agreed. A mutation-probed 173/173 suite therefore missed three agreement failures.

## 2. Proposed approach

Add two independently versioned derived controls without creating another source of product
intent. They share this design review because they exercise the same authority-preserving
compilation discipline; they do not share a publication or rollback unit.

1. **Checkpoint-bound run guidance.** A user selects a canonical
   `factory-run-guidance-selection/1` source through the already externally verified resume
   configuration under the reserved name `factory-run-guidance`. The externally anchored resume
   checkpoint contains the SHA-256 of the selector bytes in its `configuration_digests` map;
   verification re-reads the stable regular source and refuses unless the bytes re-derive that
   digest. The reserved name selects the already digested entry and is never the integrity root.
   Each entry identifies an exact,
   separately checkpoint-bound source document by name and SHA-256, classifies it as a
   `standard`, `loop`, or `recipe`, and enumerates stable obligations, intended roles, authority
   targets, an observable `behavioral | procedural | constructional` subject class, and a concrete
   classification basis. Enforcement is derived from the subject class rather than independently
   chosen by the author. Ignition reuses the already verified configuration argv,
   admits stable regular bytes, and copies exact content-addressed sources into the run. No
   ambient path, mutable branch, Kindex node, or prompt attachment selects guidance.
2. **Compile selection into existing controls.** Before ratification, the Validator writes a
   closed application record that dispositions every selected obligation as `applied` or
   `not-applicable`, with a concrete basis. A deterministic renderer inserts the exact obligation,
   source address, roles, disposition, and enforcement route into generated sections of the
   existing Product, Architecture, and Testing/Monitoring authorities. One Phase-A compiler owns
   all generated regions. Ignition metadata enumerates the generated Product-region families that
   form this run's agreement domain and binds each region digest. `agreement/1` always means
   “exactly the requirements in that explicit region set”; the later guidance release adds
   `run-guidance` to new runs without changing old agreement semantics. The compiler emits
   guidance regions before deriving the enumerated Product requirement set, then emits the
   agreement region, with disjoint markers and a fixed total order. Byte-exact verification
   refuses a stale, hand-edited, missing, or extra member. Thus those three artifacts remain the
   only intent authorities. A recipe is mechanism rather than intent: an applied recipe must bind
   a qualified Pattern Catalog entry and exact Build Plan step. Standards that claim product
   compliance must bind ratified acceptance-obligation IDs. Process loops bind named process
   checkpoints. `not-applicable` is visible in a ratified authority and is never an unrecorded
   omission.
3. **Small role projections.** Validator and resident Orchestrator can inspect the complete
   retained selection. Coder and Tester receive only applied obligations scoped to their role.
   The Tester projection is constructed from exact `behavioral` obligations only; it never
   includes a whole source document, and it excludes every `constructional` obligation regardless
   of whether the source was labelled standard, loop, or recipe. Classification and its stated
   basis are mandatory subjects of independent adversarial review, so a construction recipe
   relabelled as a standard becomes a review finding rather than an invisible authority transfer.
   Projections name the
   source and application digests and carry no unselected document. The existing dispatch task
   digest binds these bytes.
4. **Continuous application and evidence check.** The Orchestrator assessment contract gains
   the exact selection digest and a guidance state:
   `none | pending-application | routing-verified | evidence-complete | noncompliant`.
   Ordinary Phase-A work may remain pending. A pre-dispatch checkpoint requires verified
   routing; a pre-verdict checkpoint requires complete evidence. Any semantic-compliance finding or
   noncompliant state forces the Orchestrator's monotone block. The host independently verifies
   application at Phase 1 and exact evidence membership before endgame. Assessment
   state is re-derived from the exact selection, application, and evidence digests rather than
   accepted as an Orchestrator claim. A selection-digest change starts a fresh generation at
   `pending-application`; application and evidence from the prior digest is
   non-transferable. This reset is the deliberate non-monotone exception; grant effects remain
   monotone. The Orchestrator judges whether mechanically complete routing is semantically
   appropriate. Findings and blocks are run-scoped and monotone across generations. A superseding
   selection names its predecessor and must disposition inherited findings; changing the selector
   can invalidate evidence but can never erase a prior block.
5. **Agreement contract.** Add a derived `factory-agreement-contract/1` beside the Testing and
   Monitoring Strategy. For runs ignited with `agreement_contract_version` set to this schema, it
   has exactly one entry for every requirement in the complete Product Specification after all
   other generated Product regions have landed. Pre-existing in-flight runs whose ignition
   metadata predates that field remain on their prior contract; closed historical runs are never
   reinterpreted. The participant inventory is an exact, retained, digest-bound input, preferably
   generated from a route table, call graph, schema registry, or generated bindings. It derives
   classification: one participant is single-path and must cite that mechanical inventory digest;
   two or more force cross-path with no downgrade
   switch. Cross-path entries disposition version skew, data at rest, retry, duplication,
   ordering, and error taxonomy, and every applicable or not-applicable disposition carries a
   concrete basis. A cross-path entry additionally names the shared
   structural authority (if any), the semantic residue, one agreement oracle, and asymmetric
   producer-side and consumer-side mismatch mutations. The generated register is inserted into
   the signed Testing and Monitoring Strategy and byte-compared before dispatch.
6. **Phase-C meaning of end to end.** “Drive it live” remains required, but it no longer permits
   a collection of independent path tests to stand in for a relational claim. For every declared
   cross-path contract, the Validator must run the agreement oracle at the real shared boundary.
   For each mismatch direction, exhibit at least one mismatch that the relevant local suites do
   not detect and the agreement oracle does. This is an existential non-redundancy probe; never
   weaken a local oracle to manufacture the witness. If no such witness exists, show either that a
   shared structural authority carries the entire semantic residue or that the requirement was
   incorrectly mapped as cross-path, then re-ratify. Each witness binds the exact candidate,
   local-suite, and agreement-oracle digests and is stale if any changes before verdict. A claim
   that structural authority carries all residue is a mandatory independent-adversarial-review
   subject, never a Validator-only escape. Prefer a single structural authority or generated
   artifact;
   spend end-to-end tests on semantic residue that types and generation cannot carry.

Data flow:

```text
external checkpoint configuration
  -> exact selected documents + selection manifest
  -> Validator application record
  -> generated sections in the three ratified authorities
  -> role-specific projection / Pattern Catalog / acceptance obligations
  -> Validator evidence + resident Orchestrator assessment
  -> Phase 1 and endgame refusal gates
```

## 3. Failure modes

- **`refused` — source substitution.** A selector or selected document changes after the
  checkpoint. Resume configuration digest re-derivation refuses ignition, so no lane receives the
  changed bytes.
- **`refused` — membership loss.** A selected obligation disappears, duplicates, or is silently
  called irrelevant. Exact membership and generated-section byte comparison force Phase 1 to
  refuse.
- **`routed` — construction/behavior confusion.** A construction recipe is labelled behavioral,
  or observable behavior is labelled constructional. Projection rules prevent constructional
  text reaching the Tester, while the application record requires a concrete classification
  basis in both directions. Independent adversarial review owns the semantic classification; the
  host proves only routing. Constructional items remain visible as
  `routed-and-structurally-untested` in the evidence record.
- **`routed` — standard lacks real compliance.** A selected behavioral standard has an acceptance
  route but its test does not reach the requirement. Exact membership keeps the run short of
  `evidence-complete`; Validator oracle-quality review and independent adversarial review own the
  substantive adequacy question. The route alone is never described as compliance.
- **`routed` — inappropriate application.** The Orchestrator and independent reviewer examine
  every subject classification, classification basis, `not-applicable`, structural-residue claim,
  and routing choice. A semantic finding creates a monotone run-scoped block. The host does not
  claim hashes understand standards prose.
- **`disclosed` — bounded-manual inventory.** When no mechanical participant inventory exists,
  the exact inventory artifact, limitation, closure class, and criticality disposition live in
  the run's agreement/evidence record. Critical bounded-manual coverage blocks. A lower-class gap
  is disposed by normal criticality policy and is never rendered as mechanically verified.
- **`refused` — stale agreement witness.** A candidate, local suite, agreement oracle, or
  participant-inventory digest changes after a mismatch witness. Exact-subject verification makes
  the witness non-transferable and forces re-execution.
- **`routed` — two paths drift while locally correct.** Paired asymmetric mutations make
  composition the test subject; a claim that structural authority eliminates all residue must be
  independently adversarially reviewed.
- **`refused` — participant set changes.** A new participant changes the digest-bound inventory;
  the agreement register becomes stale and must be amended before dispatch or verdict.
- **`routed` — rollout/data semantics differ.** Cross-path version-skew and data-at-rest
  dispositions each require an applicable plan or concrete not-applicable basis and remain
  subjects of Phase-C execution/review.
- **`refused` — false supervision.** An Orchestrator report grants nothing. Host re-derivation, the
  unassessed-cursor gate, and run-scoped monotone findings refuse a fabricated clean state or a
  generation reset that attempts to erase a block.
- **`refused` — partial publication.** A crash after guidance admission but before ignition
  metadata is recoverable only through write-once-or-identical publication; different bytes
  refuse rather than overwrite.

These closure classes are also fields in the per-run evidence records. A `routed` or `disclosed`
item can never satisfy a schema field whose name or semantics claims mechanical refusal or proven
compliance.

## 4. Constraints

- Product Specification, Architecture Specification, and Testing and Monitoring Strategy remain
  the only product/architecture/testing intent authorities.
- Recipes remain qualified construction mechanisms and disposable Build Plan IR; selection alone
  never qualifies or authorizes a recipe.
- The guidance selector must be externally checkpoint-bound, content-addressed, stable-read,
  bounded, canonical, and per-run/per-generation. There is no ambient override.
- Coder and Tester independence remains intact; Tester never receives a whole guidance document or
  any obligation classified as constructional.
- Orchestrator retains only `block | no-op` effects and never gains grant, verdict, or close
  authority.
- Existing runs with no selected guidance continue through an explicit `none` path. Agreement is
  mandatory only when ignition metadata names `factory-agreement-contract/1`; older in-flight and
  historical runs retain their prior semantics. Published artifacts remain readable; new
  artifacts use new schema identifiers rather than silently changing old schemas.
- Every selected obligation and every Product requirement has exact closed membership. Unknown,
  stale, missing, duplicated, or extra records fail closed.
- Agreement closure is over the exact generated-region families and region digests named by the
  run, never over an implicit “current document.” The exact participant inventory digest is part
  of that subject. Single-path status requires mechanical inventory evidence; bounded-manual
  inventory is an explicit weaker closure class and cannot clear Critical coverage.
- “End-to-end” evidence names the relationship under test. A set of component tests cannot prove
  an agreement claim.
- Evidence distinguishes mechanical completeness from semantic adequacy; neither is narrated as
  the other.

## 5. Assumptions

- The resume checkpoint's externally pinned digest covers the checkpoint bytes, whose
  `configuration_digests` map covers the selector and every selected source's exact bytes. The
  accepted argv is path transport only; every consumer re-derives bytes against those digests.
- **Load-bearing:** run authors can express the portions of an arbitrary standards document they
  intend to apply as stable obligations. Factory cannot soundly infer complete normative meaning
  from arbitrary prose without another reviewed extraction step.
- **Load-bearing:** qualified Pattern Catalog, Build Plan, and ratified acceptance-obligation
  catalog artifacts are available before a selected recipe or compliance standard is called
  routing-verified.
- Phase artifacts can host deterministic generated sections before their ratification digests are
  written, as the semantic-evidence union already does.
- The existing task digest and retained runner task make a generated role projection part of the
  exact dispatched subject.
- Whether a mechanically derived participant inventory is possible is decided per surface. No
  claim about “most” surfaces is required for the control to remain honest; exceptions are
  `bounded-manual`, carry their exact limitation in run evidence, and cannot clear Critical.

## 6. Limitations

- This does not make natural-language standards machine-decidable. The user/Validator enumerates
  obligations; independent extraction and adversarial review remain necessary for recall and
  interpretation.
- Every selection/application guarantee is conditional on an obligation having been enumerated.
  Omitted normative content is not refused by this version; it is a `disclosed` recall risk in the
  run record. No failure-mode language promotes that disclosure into a mechanical guarantee.
- A digest proves byte identity, not correctness, authorship, or applicability.
- A qualified-pattern/build-step binding proves only that a recipe was routed through a qualified
  mechanism. It does not prove the produced implementation conformed to the recipe. Conformance is
  an explicit adversarial-review subject and is never implied by `routing-verified`.
- The agreement register proves that a test plan names composition; it does not by itself prove a
  future test genuinely reaches the live relationship. Existing receipts, mutation runs, Phase-C
  reading, and adversarial review provide that evidence.
- Operator-owned tmux remains a coordination surface, not a kernel isolation or qualification
  claim.
- Neither contract retrofits already ratified or closed historical runs. New agreement enforcement
  is keyed by ignition metadata; new guidance selection starts with a fresh checkpoint/generation.
- Process-receipt semantics are bounded to named checks; a custom loop that needs a new privileged
  host effect still requires an independently approved control-plane change.

## 7. Alternatives considered

1. **Attach arbitrary documents to every prompt.** Rejected: mutable, contaminating, easy to omit,
   impossible to disposition, and not tied to promotion evidence.
2. **Create a fourth “standards authority.”** Rejected: it conflicts with the three-authority
   boundary and creates precedence questions whenever a standard and ratified product decision
   disagree.
3. **Put everything in the global directive ledger.** Rejected: it is not naturally per-run,
   makes recipe mechanisms look like behavioral directives, and cannot bind Build Plan or
   acceptance evidence.
4. **Require only more end-to-end tests.** Rejected: “more” does not name the relationship and can
   reproduce the 173/173 failure. The subject must be a declared cross-path contract with two-way
   asymmetric drift.
5. **Publish agreement alone and defer guidance.** Accepted as the release boundary: agreement
   ships first because it answers a demonstrated failure and has no dependency on standards
   extraction. Guidance ships in the following release with its own schema, gate, migration, and
   rollback. They remain in one design review only to settle their shared generated-region and
   authority rules.
6. **Chosen for each independent control: compile exact per-run inputs into existing authorities
   and controls.** This preserves
   provenance and authority boundaries while making selection, applicability, delivery, and
   evidence closed and inspectable.

## 8. Open questions

- Should a future authoring helper independently extract obligation candidates from an arbitrary
  selected document twice, as semantic-union does, before the user finalizes the selector? This
  version keeps the selector explicit and records automated extraction as a follow-on rather than
  claiming complete standards parsing.
- Which target classes lack a mechanically derivable path inventory? Record concrete cases from
  dogfood before narrowing or broadening the allowed `bounded-manual` escape.
- Can the core acceptance-obligation report become the sole compliance receipt for all standards,
  or do genuinely procedural loops need a separate qualified process-receipt family? Dogfood will
  identify whether the latter carries unique evidence rather than duplicating a checklist.

Boundary decision: selected guidance permanently remains externally checkpoint-bound run
configuration. It is not part of Stage-E's verbatim execution request. If a selected obligation
changes product behavior, that effect acquires authority only when compiled into and ratified with
the three phase artifacts; keeping configuration provenance separate from intent prevents the
selector from becoming a fourth authority.

## Worked forcing example: `res-r1`

The acceptance fixture for `agreement/1` reconstructs the observed reservation relationships,
rather than merely testing a synthetic two-function toy:

| Requirement relationship | Mechanically inventoried participants | Derived class | Agreement subject |
|---|---|---|---|
| one availability decision gates quote and commitment | `quote`, `hold` | cross-path | identical eligibility for the same property/range, including prep-time occupancy |
| one guest identity governs storage, update, and erasure | `create-contact`, `change-guest`, `erase` | cross-path | absent email never aliases unrelated subjects or destroys an existing identity |
| one hold lifecycle governs release, confirm, and expiry telemetry | `release`, `confirm`, `expired-metric` | cross-path | released is not expired and never increments the TTL-lapse metric |

The fixture fails if any participant is removed, if any relationship is called single-path, if one
asymmetric mismatch direction is absent, or if a witness is reused after its local-suite digest
changes. It records that this mapping is reconstructed mechanical evidence from the route/call-site
inventory for the run; if that inventory cannot be produced, the example is explicitly
`bounded-manual` and therefore cannot clear a Critical surface. This is the counterfactual the new
control must satisfy: the pre-change `res-r1` register cannot reach a clean pre-verdict state.

---

Per architecture-review protocol: if you identify a flaw, ground it in a cited invariant,
design principle, or worked example. Ungrounded assertions are not adversarial review —
they are disagreement. Enumerate distinct critiques as numbered items so each can be
resolved individually.
