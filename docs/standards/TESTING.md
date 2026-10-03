# How We Test

A test is evidence only if it would have failed had the system been wrong.

Tests are a means. The deliverable is evidence that the system enforces its contracts, controls and invariants — evidence that would have gone red had it not. A test that passes whether or not the code is correct is worse than none: it consumes the reviewer's trust, appears in coverage, is cited at the gate, and lies. The governing maxim:

> **A test that cannot fail is worse than none.**

Companions: [How We Write Code](HOW-WE-WRITE-CODE.md) gives a test its shape (W19–W20). [How We Review](REVIEW.md) checks a suite in pass C9. [Oracle Quality](../practices/oracle-quality.md) is the field manual for proving a guard guards, with the worked failures. Rule IDs (T1–T9) are stable; the Tester cites them in its ledger and reviewers cite them in findings.

---

## The four integrity properties

Everything below serves four properties. A suite missing any one of them is decoration.

1. **Reachability.** A test reaches and exercises the code it claims to protect. A security test that 403s at an unrelated earlier gate tests nothing.
2. **Falsifiability.** For every test you can name a specific mutation of the production code that turns it red. If you cannot, it asserts nothing.
3. **Isolation.** Tests pass in any order, in parallel and individually. Shared mutable state and ordering dependence hide real failures and manufacture phantom ones.
4. **Oracle independence.** The expected answer comes from the signed target, never from the code. The other three rest on it.

---

## Rules

**T1. The oracle comes from the target, never from the code.** A test whose expected answer was inferred from what the code does passes whenever the code is self-consistent, including when it is uniformly wrong. Reading the implementation to learn what to assert produces no oracle at all: the thing being checked has become the thing doing the checking. So the property is structural, not attitudinal:
- Expected behavior is derived from a signed artifact — the Product Specification, the Architecture Specification, the Testing and Monitoring Strategy — and fixed before any implementation is inspected.
- The Tester does not read the implementation and cannot reach the Coder ([SOFTWARE-FACTORY.md §6](../SOFTWARE-FACTORY.md), *The oracle is independent*).
- An expectation that could only have come from running the code is not evidence. A golden file captured from the implementation under test is self-certification with extra steps, unless the human ratified the captured values against the specification.

**T2. Assert the capability, not the mechanism.** The signed target states what must be true; the mechanism that satisfies it is guidance the Coder may improve on. Assert *"does the user ever get stuck with no recourse?"*, not *"is there a Continue button?"* The first captures the whole class of failure and survives re-implementation. The second pins the suite to a mechanism the system may outgrow, passes while the capability breaks by another path, and goes red when the implementation legitimately changes. A named output — a failure code, an endpoint, a field — is asserted as itself only where it is a ratified external contract others build against, because there the mechanism is the promise.

**T3. Boundary tests during the loop; unit tests after validation.** Tests written during the build are integration-level and assert observable effects at a boundary, because that is what the signed artifacts constrain. Unit tests come after the implementation shape has settled and the boundary behavior is proven; written earlier, they encode an incidental structure and then resist the refactor they should have survived. Contract tests are the exception: a contract is an interface promise, so its tests are authored in planning and must fail against an empty implementation. A contract test that passes against a stub is fraudulent.

**T4. Prove each oracle is non-vacuous.** Falsifiability asks whether a test *can* go red. Oracle quality asks whether it goes red *for the requirement*. Three questions, split by who can answer them:
1. *Does the fixture reach the code path?* Instrument it and confirm. A cold start, an empty set, a zero-length window, a disabled feature or a clock that never advances makes the operation a no-op, and the test is green without running the logic. — Tester, at authoring.
2. *Does the assertion discriminate?* Feed it the value the requirement forbids and watch it reject; feed it the value the requirement demands and watch it accept. — Tester, at authoring.
3. *Does it fail at base for the reason the requirement names?* This is an observation, attributed by the party that ran the suite against a cited run, revision and failing assertion. An author's assurance that it *would* fail for the right reason is not an answer. — Validator, from the base run.

Clearing the first two lets the Tester hand over. It does not make the requirement verified. A requirement whose base-run attribution was never made carries no evidence at the promotion decision.

**T5. Controls first, and every control proven reachable.** Build the cross-cutting suites before the convenient ones: authorization and isolation (refused *after* reaching the check; wrong-tenant principal isolated in-query; then the positive case), atomic commit (force a failure between two writes that must commit together; neither appears), deterministic rules (one test per rule and per negation; same inputs, same decision), one check per hard constraint. For each exploit class, prove the payload *arrived* at the protected path, assert the protection *engaged*, then disable the protection and watch the test go red. A green unit suite with a missing isolation test is a system with no proof that one tenant cannot read another's data.

**T6. Hermetic suites prove logic; live gates prove the system.** Hermetic tests are authoritative for the exact revision and run on every change. They cannot catch a missing runtime dependency, a grant that exists in code and not in deployed config, or a datastore that behaves differently from its stand-in. Integration tests run against a real, disposable datastore of the production engine and version, created and destroyed per run. A live smoke gate runs against a running instance. Never let a green hermetic suite stand in for a live gate.

**T7. Determinism is class-scoped, and retry is recovery, not search.** On a Critical surface a non-deterministic test is not evidence: no time or ordering dependence, no shared mutable fixture, no network outside the disposable environment, no sleep-and-hope, no retry wrapper or tolerance window added to make the suite stable. A Critical flake is quarantined, the behavior it asserted becomes unverified, and promotion blocks until it is fixed; a manual rerun is a new, separately recorded run that does not erase the red one. Standard tests may spend a declared flake budget, with a named owner and expiry per quarantine. Cosmetic tests may retry, keeping the flake visible. Criticality classes are defined in [SOFTWARE-FACTORY.md §3.5](../SOFTWARE-FACTORY.md).

**T8. Repairs are bounded from both sides by the running system.** Correction work has an oracle a new build lacks: the running system, correct on everything but the reported fault.
- *Red-now:* the new tests fail against current main, at least one on the defect itself.
- *Green-now:* they pass against current main on everything unrelated.

Both are recorded runs — run id, exit code, failing assertion, revision — not narrated intentions. Red-now proves a test *can* fail, not that it is *about* the requirement; record why the failing assertion is the one the requirement names (T4.3). A green-now guard that comes back red is never silently reclassified as a red-now target: "this working behavior was wrong" is a human decision against a signed amendment, never an executor's inference from its own fix. A test that passed on the trusted baseline and fails now is disposed by signed authority, never by preference ([SOFTWARE-FACTORY.md §10](../SOFTWARE-FACTORY.md), *When an existing test fails*).

**T9. Gates prevent regression; adversaries find defects.** These are different jobs, and only one of them finds what nobody thought to ask about. In field use, a run whose every gate was green — a red-now/green-now pair, a 1,659-test rail, the ship target, an isolation proof, five live probes and changeset hygiene — shipped a release that was wrong twice over, and every defect that mattered was found by an adversary. The gates were not worthless: they proved the absence of regression, which is why the fixes could be made quickly. But a gate can only re-ask a question someone already wrote down, so a process assembled only from gates ships its defects with a clean bill of health. Run every gate; then point adversaries at the suite itself — finders enumerate tests that could not fail, independent refuters try to name the mutation that turns each red, and whatever survives blocks. Read a green board as "nothing known broke," never as "nothing is broken."

---

## Before you hand over a suite

1. Does every row of the ratified test plan map to a concrete test, and is every missing or changed row raised as a specification defect rather than quietly replaced with a cheaper test?
2. For every test, what is the named mutation that turns it red, and did you watch it go red?
3. For every requirement-carrying test: does the fixture reach the path, and does the assertion discriminate? (T4.1–T4.2)
4. Did every security test prove the payload reached the protected path before asserting it was refused?
5. Do the tests pass in any order, in parallel and alone?
6. Is any expectation copied from the code's output rather than derived from a signed artifact?
7. On a Critical surface, is there any clock, sleep, retry or tolerance window holding the suite green?
8. Was the whole suite scanned for tests that cannot fail, and is every finding cleared or blocking?

## Provenance

Generalized from the Testing & Test Integrity phase of the retired Production-Grade Build Playbook (Phase 5 §1, §1.1, §4.1–4.3, Steps 1–8) and its foundations chapter (*Gates prevent regression; adversaries find defects*). This document is now the canonical statement; the playbook is archived.
