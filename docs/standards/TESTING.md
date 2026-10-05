# How We Test

Wrong code must not be able to stay green.

Everything here serves that one aim. A test is evidence that the software does what was promised. A suite that passes whatever the code does is ceremony, and ceremony is worse than nothing because it gets cited.

This is the overview. It says what a good test is, which kind to write for which claim, and what code owes its tests. The Tester's procedure, ledgers and gates live in the Tester directive ([`prompts/test.md`](../../prompts/test.md)), and this document does not repeat them. Strategy rows, reviews and verdicts cite the rules below by ID. Where a rule leans on another document the ID is given: W for [How We Write Code](HOW-WE-WRITE-CODE.md), H for [Whose Word Counts](AUTHORITY.md), and I, E and S for the [doctrine kernel](../DOCTRINE-KERNEL.md). A *stated item* is a requirement with authority under H1–H8: what the human said, latest first, with any exception they made.

---

## Evidence

**T1. The oracle comes from outside the code.** The oracle is where the expected answer came from. A test whose expected answer was read off the implementation, or recorded from a run of it, checks that the code agrees with itself (W19, I3).[^oracle] Acceptable sources, strongest first:

1. A law of the domain or of arithmetic. Money is conserved. `decode(encode(x))` is `x`.
2. A stated item that gives the outcome ([AUTHORITY.md](AUTHORITY.md) H1).
3. A reference model: a slow, obvious implementation of the same contract.
4. A relation between runs: how the output must move when the input moves (T12).
5. Agreement between two paths that share one decision.
6. A captured baseline that a human confirmed against the specification.

Laws, models and relations are requirements. Each is cited to a stated item like any other assertion (H7), and every expected value records how it got there:

- *Direct.* A stated item gives the value.
- *Derived.* The value is an exact, mechanical consequence of a stated rule: the arithmetic of a stated fee, the edge a stated range implies. Anyone applying the rule gets the same answer, so it needs no further approval. The derivation is written beside the test.
- *Proposed.* A relation or law the items do not state, inferred because it seems true. It is a question to the human (`FACTORY_QUESTION`, T37), and the test counts as no evidence until the human states it.

If two careful readers could derive different values, the value is proposed, not derived. Raise it. Do not supply it yourself.

Independence comes from separate derivation, not from ignorance. The Coder and the Tester share the stated intent, the architecture, and the interface and schema contracts: what the system promises, where its boundaries are, what may vary and what must not. Neither sees the other's work or the other's reasoning. Each derives its own reading from the shared intent. When the two readings disagree, that is a finding about the specification. Neither lane resolves it by conforming to the other. The test of a good contract is whether two parties who cannot talk can build and test the same thing from it.

**T2. Grade every test on three axes.** *Independence*: where did the expected answer come from? *Fidelity*: how much of the real system ran? *Sensitivity*: which wrong implementation turns this test red? A test is as strong as its weakest axis. A browser test asserting that the page loaded has fidelity and nothing else. A mocked test with a perfect oracle proves the mock.

**T3. Tests hold intent still.** A test changes only when a later stated item changes what it asserts (I14, H3).[^unchanging] When a code change needs a test change and no stated item moved, one of two defects is present: the code broke a promise, or the test was pinning structure. If every change to the code arrives with a change to its tests, nothing is being tested. The author of a change believes the change is right, so an author who can edit the tests edits them until they agree.[^impossible] That is why the Coder cannot write to the tests, and why a change set that edits a test without citing the superseding item does not promote.

The rule binds by authority:

| Authority | What it protects | Changes when |
|---|---|---|
| Normative | A stated item | The human supersedes it (H3) |
| Policy | A stated policy (T23) | The human amends it |
| Regression | A defect that escaped once | Never, while the behavior stands |
| Characterization | What legacy does today (T30) | A human reviews the change |
| Exploratory, diagnostic | Nothing; they find and localize defects | Freely, and they never count as evidence |

Every test declares its authority. A test that declares none is normative.

**T41. Freeze the expectations, then append only.** The Tester's plan (every invariant row, expected value and falsifier) is hashed and recorded before any candidate exists. The runtime's freeze of Coder and Tester outputs before review ([SOFTWARE-FACTORY.md](../SOFTWARE-FACTORY.md), freeze before review) is the same rule applied later. After the freeze, findings may add tests, attacks and examples. They never move an expected value; only a later stated item does that (T3). Every test is labeled *pre-freeze*, written before any candidate existed, or *post-freeze*, with what it was added from. The Tester stays blind to the implementation throughout (I3): a post-freeze test comes from a failure outcome, a finding or a new item, never from reading the code. The label lets the verdict weigh a post-freeze test as what it is, a test written knowing that a candidate exists.

**T4. Assert the promised outcome at the most stable boundary that shows it.** The persisted row, the event, the money moved, the response the caller acts on. Not call counts, private methods, log wording or the status envelope. Ask of every assertion: would a correct rewrite of the internals break it? If so, it tests structure.[^desiderata]

---

## Which test for which claim

**T5. Start from invariants, not from files.** A suite written one test file per source file is over-constrained where code is easy to reach and empty where it matters. Write one Strategy row per invariant:

```yaml
- invariant: A guest is never charged for dates that were not confirmed.
  cites: product-spec@<digest>#INV-7
  oracle: law              # a T1 source
  kind: sequence           # from the table below
  fidelity: real database; payment transport faulted at the seam
  falsifier: capture runs although confirmDates claimed zero rows
  class: Critical          # decides what a gap does (I5)
```

| Kind | Proves | Does not prove |
|---|---|---|
| **Behavior** test at a boundary | The promised outcome occurs through real units | Rare inputs, histories |
| **Property** | A rule holds across the input space | That the rule is the right one |
| **Sequence** | Invariants survive histories: retries, duplicates, reordering | Behavior outside the modeled commands |
| **Pin** | A frozen unit returns exactly what it did | Anything about units meant to change |
| **Seam** | Two correct units work together (W20) | The dependency's real protocol |
| **Boundary** | Our code speaks the real database and the real wire | The provider's business behavior |
| **Compatibility** | Two deployables agree on message shape | That the exchange accomplished anything |
| **Agreement** | Two paths cannot read one shared decision differently | Either path alone |
| **Forcing** | A control fires and signals (W8, W20) | The nominal path |
| **Adversarial** | Hostile input cannot break a safety rule | Functional correctness |
| **Anti-gaming** | A degenerate implementation cannot satisfy the metric | The honest implementation |
| **Policy** | Every member of a class obeys its rule (T23) | Member-specific behavior |
| **Live probe** | It works in the running system | Anything not exercised |
| **Characterization** | What legacy does today | That legacy is right |

Reading an invariant tells you the kind:

- Stated for all inputs of a unit that decides: property, plus exact examples at the limits.
- Says never, always, exactly once or eventually: sequence.
- Names a frozen calculation with given values: pin.
- Crosses to a dependency: boundary. Crosses between our deployables: compatibility and behavior.
- Says "every X must" about code: policy.
- Has no single right answer: relation (T12).
- Describes a refusal or a control: forcing, with proof it reached the check.

**T6. Choose the cheapest test that can expose the failure.** An arithmetic error needs a function call, not a cluster. SQL semantics need the real database engine. Serialization needs a real client talking to a loopback server. A double booking needs two concurrent claims against real Postgres (W11). Buy realism per risk.

**T7. The pyramid is a cost model, not a truth model.** What a test touches decides when it can run (T34). It does not decide whether the assertion is right. We set no ratio of unit to integration tests and no coverage target. Coverage shows code that no test executed. It does not show the tests would notice a defect: with suite size controlled, coverage correlates only weakly to moderately with fault detection.[^coverage] Use it to find holes.

**T8. Properties for anything that decides.** A unit (W2) takes arguments and returns a value, so its rule can be stated for all inputs and checked on thousands. Shapes to look for: round trip, idempotence, conservation, bounds, monotonicity, order-independence, agreement with a reference model. Keep exact examples at every limit: at, just below, just above, empty, maximum, duplicate.

```ts
// cites: product-spec@<digest>#INV-12  nightly amounts sum to the quoted total
fc.assert(fc.property(cents, nights, (total, n) =>
  sum(allocate(total, n)) === total))
```

A failing generated case is recorded with its seed and kept as a permanent example.[^fastcheck] Each generated case runs in its own state: a fresh instance or a rolled-back transaction. State that leaks between cases makes a failure depend on the cases before it, and then shrinking cannot reproduce it.

**T9. Pins are for units meant to stay put.** A pin is fixed data in and a literally compared result out, with no doubles. It is the right test for a unit whose contract is agreed and whose behavior is meant to be immutable: a fee calculation, a date-overlap predicate, a parser. A pin on a unit whose shape has not settled encodes that shape and then resists its changing, so pins below the contract level wait until validation. A pin that breaks under a refactor that kept the behavior was pinning structure. Delete it.

**T10. State needs sequences.** A defect that needs a history cannot be found one method at a time. Take the states and legal transitions from the Architecture Specification, generate command sequences against the real component and a simple model, and check the invariants after every step.[^fastcheck] Put retries, duplicate deliveries and reordering in the command set. Drive interleavings with a scheduler, never with sleeps.

**T11. A compatibility test proves the plug fits. It does not prove the current.** Contract tests between deployables (the pact.io kind, unrelated to our Pact) check that both sides understand the messages. They do not check side effects.[^pact] A 200 from a pass-through says nothing about what happened downstream. Every compatibility test has a behavior test elsewhere that asserts the effect.

**T12. Where no exact answer exists, test the relation.** Search results and ranked output have no single expected value. They still obey relations: adding a filter returns a subset; reordering independent inputs changes nothing; raising the guest count never adds a listing. Run the system twice and assert the relation.[^metamorphic]

**T13. Fuzz parsers and trust boundaries.** Anything that reads bytes we did not write gets a generated-input target with one property: it returns a value or a typed error (W8), and never crashes, hangs or accepts malformed input. Seed it with the inputs that break parsers: empty, null bytes, NaN, infinity, negative zero, unnormalized Unicode, deep nesting, oversized lengths. Keep the corpus. Every finding becomes an example.

---

## Fidelity

**T14. Run the real thing.** In order of preference: the real component; the real component in-process or in a disposable container; a fake that passes the real component's contract suite; a stub that raises a fault around the real component; a mock. Every step down is entered in the mock ledger with what stands behind it.[^doubles]

**T15. A fake earns trust by passing the real thing's tests.** One contract suite runs against both. A fake with no such suite drifts, and tests against it pass on fiction.

**T16. Substitute at declared seams. Never patch.** No monkey patching, no module replacement, no reaching into privates. With duck typing a patched test can pass while exercising nothing real. It verifies that you can patch. If a test cannot run without patching, the dependency was not passed in (W7). Raise a testability defect. The same holds in reverse: production code never asks whether it is under test. A branch on a test flag or environment is a second implementation that only the tests run. It is a structural policy (T25).

**T17. Doubles set conditions. They are not the evidence.** Use a double to make a timeout happen. Do not cite its call log as proof that the real boundary works. The one sanctioned order assertion is the composition test (W20), observed at the edge.

---

## What code owes its tests

The Coder owns these. The Tester raises a testability defect when a contract makes one impossible. T18 to T20 are enforced as policies (T23).

**T18. Keep units small enough to exhaust.** Cyclomatic complexity counts the independent paths through a unit. It is also the number of tests needed to exercise every decision outcome.[^nist] Above 10, the unit needs a written justification and properties strong enough to cover its paths. Above 20, it is an architecture finding unless an exception (T28) covers it. The aim is a decision space small enough to exhaust, not a number. Splitting a coherent algorithm into fragments to pass the count makes it harder to check, and that is gaming the metric. A composition scores 1 because it does not decide (W2).

**T19. Keep behavior local.** What a unit does is settled by its arguments and its declared dependencies, all visible in its signature. A test needs nothing the signature does not name. Module state, environment reads, ambient clocks and singletons are hidden inputs. When arranging a test takes more lines than the call and its assertions, the unit depends on too much.

**T20. Compose. Do not reach for globals.** Dependencies are passed in (W7). A global is shared by every test, couples their order, and can be replaced only by patching (T16). In Effect a function's requirements are part of its type, so the type lists what a test must provide and what the classifier reads (T24).

**T21. Own time, randomness and identity.** The clock, the random source and the ID generator are injected. Tests advance a test clock. They never sleep and never let a real backoff run.[^testclock] A test waiting on background work awaits the signal that the work completed, such as a drained queue or a resolved deferred. It does not wait a while and look. Whatever is controlled does not have to be masked (T29).

**T22. Let types and the database remove tests.** A state the type forbids, or a row the constraint rejects (W10), cannot occur. It needs one test proving the constraint exists and none for the cases it rules out. The constraint removes the combinations, not the consequence: one application-level test still shows that an attempt to break it ends in the promised disposition and signal (W8, W20).

---

## Class rules

**T23. Test a universal rule once, for the whole class.** A policy names a class of code, how members are detected, and what each member owes. The harness enumerates the members at judge time and applies the policy to each. Nobody writes the per-member test, and a method written tomorrow inherits its obligations the moment it exists. Policies are intent. Humans state them and agents only propose them (S4, H1). Policy tests are derived from the policy text, never from the members they check.

**T24. Classify by fact, and fail closed.** Detection uses what the compiler and the import graph can state: what a function's type requires, what a module imports, what it exports, where it lives. Judgment is not a detector. Changed code that matches no class fails the run. Unclassified never means no obligations. The class `ordinary` exists and is claimed with a reason. Registration by inheritance is a convenient way to list members, but it is opt-in. Code that never inherits never registers, so registration can enumerate members but cannot decide that no rule applies.

**T25. A policy can owe three things.**
- *Structural*: checked without running anything. Only the transport may import a network client.
- *Behavioral*: a generated test run against every member.
- *Bespoke*: a hand-written test of a named kind must exist and cite the member. Every state machine has a sequence test.

**T26. Make the violation impossible before you test for it.** Route the behavior through one component that performs it, test that component well, and forbid every other route. A per-method test asserting that the logger was called is the brittle, structure-pinning test this document exists to prevent.

**T27. Every policy has a canary.** A fixture that breaks the rule must be caught. A policy whose canary passes is switched off, whatever its report says.

**T28. Exceptions are data.** An exception names the policy, the target, the reason, an owner, the compensating evidence and an expiry. An expired exception fails. For legacy code, list the existing violations, refuse new ones, and let the list only shrink.

The worked case: *all I/O to external APIs captures the raw request and response.*

```yaml
- class: outbound_external_io
  detect:                           # facts, not judgment
    requires_any: [HttpClient]      # the function's requirement type
    imports_any: [fetch, node:http, undici]
  structural:
    - only ExternalTransport may import a network client
  behavioral:                       # generated, run on every member
    - against a loopback server, one call yields one capture record
      holding the request and the response, after redaction
    - timeout, refused connection and 5xx each yield a capture record
      and a typed error (W8)
  bespoke:
    - a boundary test citing the member exists
  canary: fixtures/policy/raw_fetch.ts      # must be caught

# exceptions
- policy: outbound_external_io
  target: src/vendor/stream_client.ts
  reason: the vendor SDK owns the socket
  owner: integrations
  compensating: [egress-audit, vendor-sandbox-probe]
  expires: 2027-01-31
```

"Raw" takes one qualification. Capture what is needed to reconstruct the exchange, after redaction: credentials and secrets removed, headers by allowlist, bodies protected where they carry personal data.[^otel]

Starter classes:

| Class | Every member owes |
|---|---|
| Pure unit (W2) | Properties or pins; complexity over 10 justified (T18); no effects in its type |
| Composition | One order test; no branching |
| Outbound external I/O | The approved transport; capture; timeout and error paths; a boundary test |
| Persistence write | The real database; claim, not check (W11); an atomicity test |
| Effect that moves money or leaves the system | An idempotency key stable across retries (W12); a duplicate-delivery test |
| Authorization boundary | Unauthorized refused after reaching the check; wrong tenant refused in-query; the positive case |
| State machine | Declared states; a sequence test; exactly one terminal state |
| Parser or decoder | Valid and invalid examples; round trip; a fuzz target |
| Adapter at the edge | No business decisions: serialization, transport and error translation only; a boundary test |
| Public API | A behavior test; a compatibility test; the error contract (W8) |
| Disposition boundary (W8) | A forcing test asserting the disposition and its signal |
| Signal emitter | No tier in the signal; an alerting rule for every signal it can emit, or a declared metric-only status |
| Module boundary | Imports from outside the module reach only its public entry points; no import cycles; structural, with a deep-import fixture as the canary (T27) |
| Handler of personal or secret data | A redaction test over logs, captures and errors |
| `ordinary` | Nothing extra; claimed with a reason |

---

## Variation

**T29. Mask deliberately.** Data expected to vary is excluded from exact comparison by one normalizer per payload type, never by a regex in a test body. Every masked field carries one of three statuses: *irrelevant*, with the reason; *checked by a weaker property*, such as a valid UUID or a timestamp inside the operation's interval; *checked elsewhere*, citing the test. Everything unmasked compares exactly. A blanket ignore is a neutered test.

Mask identity by reference, not by format. The normalizer replaces each distinct value with a numbered token, the first UUID it meets with `<uuid-1>` everywhere it appears, the next with `<uuid-2>`. A format check alone passes when `booking.guestId` carries the host's id. Numbered tokens fail it, because the two fields that must hold one id now hold two tokens.

```ts
const normalizeQuote = mask<Quote>({
  id:          valid(isUuid),              // weaker property
  generatedAt: within(operationInterval),  // weaker property
  traceId:     irrelevant("not part of the quote contract"),
})                                         // all other fields compare exactly
```

**T30. A golden file is a requirement.** A recorded output is an oracle only where a human confirmed its values against the specification. An updated snapshot is never accepted from the lane whose change moved it. Anything else recorded from the implementation is a characterization test. It is useful for holding legacy behavior still during a port (W16), it is labeled as such, and it never counts as evidence of correctness.

---

## Testing the tests

**T31. Every test names what would turn it red.** The falsifier is a specific wrong behavior, stated in contract terms. The run proves it by breaking the code and watching that named test fail (Gate D). Mutate changed code on every candidate and treat survivors as findings.[^mutation]

Mechanical mutants flip operators and drop statements. They rarely produce the defect an invariant exists to prevent. So every Critical invariant also gets a semantic falsifier: a plausible implementation that violates it the way a real mistake would. Examples: a retry that mints a new idempotency key; a charge taken before the dates are confirmed; a tenant filter applied after the query instead of in it; an `Authorization` header written to the capture record; a permanent error retried; the last page of results silently dropped. The evidence must reject each one. Mutation score is not a target either. It measures sensitivity, and a suite can kill every mutant while asserting the wrong requirement.

**T42. Declare a mutant before applying it.** Each mutant states, before it runs, which invariant it breaks and which named test must turn red. It touches implementation only, never a test, a fixture, a mask or an oracle. A mutant declared after its result was read, or one that reddens some other test, shows that the suite is alive, not that the named requirement is covered. Mutation evidence belongs to the Validator's lane (Gate D), never to the author of the tests ([SOFTWARE-FACTORY.md](../SOFTWARE-FACTORY.md), mutation evidence belongs to the Validator).

**T32. A repair is red first.** New tests fail on the unfixed code, at least one of them on the defect itself, and pass on everything unrelated. A regression test never seen to fail has not shown that it can.

**T33. A flake is a defect.** No retries, sleeps or tolerance windows. A test rerun to green is a sample, not a result (E4). If a behavior cannot be tested deterministically, that is a testability defect in the specification. Quarantine is an exception under T28, with an owner and an expiry.

---

## Speed

**T34. Size schedules. It does not rank.**

| Size | Touches | Runs |
|---|---|---|
| Small | One process. No network, database or sleep. | Every save, every candidate |
| Medium | Real local dependencies: the database, loopback HTTP | Every candidate |
| Large | The deployed system with disposable externals | Before promotion |
| Continuous | Fuzzing, long sequences, whole-repository mutation | Off the critical path; findings return as examples |

**T35. Fast and isolated by construction.** Each test creates the state it depends on and reads only the rows it created. Tests pass alone, in parallel and in random order, with no shared mutable fixtures. Pure cores (W7) run in microseconds, which is what lets a property run thousands of cases.

---

## What not to test

**T36. Leave these out.**
- The framework, the ORM, the vendor SDK. Test our use of them at a boundary.
- Private helpers. They are covered through the unit that owns them.
- What the compiler already proves.
- The order of calls inside a unit.
- One invariant at three layers. Test it once at the cheapest layer that can expose it, plus a live probe on Critical surfaces.
- A whole payload when two fields are the promise.
- The double.

A test that raises no confidence in a promise costs run time and reviewer trust, and someone will later "fix" it on the assumption that it mattered.

Deleting a test that no longer adds evidence is healthy. Record why. The deletion must leave every invariant's evidence standing: what the test asserted is still asserted at another layer, or its item was superseded (T3). A regression test stays while the behavior it guards stands.

---

## When a test goes red

**T37. Classify before anyone edits.**

| Finding | Route |
|---|---|
| The code violates a valid item | Coder fixes, from the Validator's finding: the item violated and the behavior observed |
| The architecture cannot satisfy the item | Back through the architecture phase; the Coder holds |
| The stated item changed | Tester re-derives the test from the latest statement (H3) |
| The test asserts structure, not a promise | Tester rewrites or removes it and records why |
| The test is nondeterministic | Tester repairs it; if it cannot be made deterministic, testability defect |
| The artifacts are silent or in conflict | Human, by the spec-defect path |
| No oracle exists: no stated item, law, relation or confirmed baseline says what correct is | Unverifiable. A question to the human (`FACTORY_QUESTION`), naming the decision needed. Never a guessed assertion |
| A green-now guard is red on unrelated behavior | Human (I15) |

The loop "run, edit the expectations, run again" is forbidden in every lane.

**T38. Generated tests are candidates.** A test written by a model is a hypothesis until it cites its item (T1), names its falsifier (T31) and is seen red. When Meta filtered model-written tests, 75% built, 57% passed reliably and 25% added coverage, and the tool kept only passing tests because it had no oracle to say a failing one was right.[^meta] Passing is the one property a self-agreeing test has by construction. Green is also weak evidence when the suite is weak: in one study, 29.6% of patches that passed a repository's tests behaved differently from the reference fix.[^swebench]

---

## Production

**T39. Production closes the loop.** Some behavior exists only under real traffic, real latency and real provider failure. Critical invariants get a live probe before promotion and a monitor after it. Every escaped defect becomes a test at the earliest layer that could have caught it, and a policy where a class rule would have prevented it.

---

## The evidence statement

**T40. Report evidence, not a count.** "All tests pass" is a fact about the suite. Every invariant ends in one of four outcomes: *verified*, the evidence holds; *falsified*, the candidate failed it; *gap*, required evidence was not produced; *unverifiable*, no oracle exists (T37). No other word describes an invariant. The handover and the verdict state, per invariant:

- the outcome;
- the item cited;
- the kind and the fidelity;
- the falsifier, and the run that showed it red;
- each double and what verifies it;
- each mask and its status;
- the policies applied and the exceptions in force;
- what was not run, and why;
- which of its tests are pre-freeze and which post-freeze (T41);
- what remains uncertain.

**T43. Watch the suite's health, with no single score.** Each signal is read on its own:

- normative-test churn against requirement churn: tests moving faster than items is T3 failing;
- canary health: every policy canary still caught (T27);
- the rate of changed code that matches no class (T24);
- exception count and age, and whether each legacy list only shrinks (T28);
- mask coverage: how much of each payload is excluded from exact comparison, and with which status (T29);
- the post-freeze share of the evidence (T41);
- surviving and undeclared mutants (T31, T42).

Fold them into one number and the number becomes the target, as coverage (T7) and mutation score (T31) do.

---

## Before you commit a test

1. Which stated item, rule or law does it cite (H7)?
2. Where did the expected value come from (T1)?
3. What wrong behavior turns it red, and for that reason (T31)?
4. Does the fixture reach the code path, and can the assertion fail?
5. Would a refactor that keeps the behavior break it (T4)?
6. What is real, what is a double, and what stands behind each double (T14)?
7. What is masked, and with which status (T29)?
8. Does it pass alone, in parallel, in random order and without real time (T35)?
9. Was it written before the freeze or after, and if after, from what (T41)?

A missing answer is a gap. List it. Do not ship it as coverage.

---

## Tools

| Need | TypeScript and Effect | Python |
|---|---|---|
| Properties, sequences, interleavings | fast-check: `property`, `commands`, `scheduler` | Hypothesis, including its stateful machines |
| Seams | Effect `Layer` | Constructor injection |
| Time | Effect `TestClock` | An injected clock |
| Classification | Requirement types through the compiler API; the import graph | `ast`; the import graph |
| Sensitivity | The factory's `mutate.sh` (Gate D) | The same |

---

[^oracle]: Barr, Harman, McMinn, Shahbaz and Yoo, "The Oracle Problem in Software Testing: A Survey," IEEE TSE 41(5), 2015. https://discovery.ucl.ac.uk/1471263/ (checked 2026-10-03)
[^unchanging]: *Software Engineering at Google*, ch. 12, "Unit Testing": the ideal test never changes unless the requirements of the system change. https://abseil.io/resources/swe-book/html/ch12.html (checked 2026-10-03)
[^impossible]: Zhong, Raghunathan and Carlini, "ImpossibleBench: Measuring LLMs' Propensity of Exploiting Test Cases," 2025. Agents given tests that contradict the specification pass them by modifying tests and similar shortcuts; how much access the agent has to the tests changes the rate. https://arxiv.org/abs/2510.20270 (checked 2026-10-03)
[^desiderata]: Kent Beck, Test Desiderata: tests should be behavioral and structure-insensitive. https://tidyfirst.substack.com/p/desirable-unit-tests (checked 2026-10-03)
[^coverage]: Inozemtseva and Holmes, "Coverage Is Not Strongly Correlated with Test Suite Effectiveness," ICSE 2014. https://cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf (checked 2026-10-03)
[^fastcheck]: fast-check documentation: properties, shrinking, model-based commands and scheduling. https://fast-check.dev/docs/introduction/ (checked 2026-10-03)
[^pact]: Pact documentation, "Contract Tests vs Functional Tests": a contract test does not check for side effects. https://docs.pact.io/consumer/contract_tests_not_functional_tests (checked 2026-10-03)
[^metamorphic]: Segura, Fraser, Sanchez et al., "A Survey on Metamorphic Testing," IEEE TSE 42(9), 2016. https://eprints.whiterose.ac.uk/110335/ (checked 2026-10-03)
[^doubles]: *Software Engineering at Google*, ch. 13, "Test Doubles": prefer real implementations; fidelity. https://abseil.io/resources/swe-book/html/ch13.html (checked 2026-10-03)
[^nist]: Watson and McCabe (ed. Wallace), *Structured Testing: A Testing Methodology Using the Cyclomatic Complexity Metric*, NIST SP 500-235, 1996: tests required equal the cyclomatic complexity; limit of 10. https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication500-235.pdf (checked 2026-10-03)
[^testclock]: Effect documentation, `TestClock`. https://effect.website/docs/v3/api/effect/TestClock (checked 2026-10-03)
[^otel]: OpenTelemetry semantic conventions, HTTP attributes: capturing all headers is a security risk; require explicit configuration. https://opentelemetry.io/docs/specs/semconv/registry/attributes/http/ (checked 2026-10-03)
[^mutation]: Petrović, Ivanković, Fraser and Just, "Practical Mutation Testing at Scale: A View from Google," IEEE TSE, 2021: mutate changed lines, surface survivors in review. https://research.google/pubs/practical-mutation-testing-at-scale-a-view-from-google/ (checked 2026-10-03)
[^meta]: Alshahwan et al., "Automated Unit Test Improvement using Large Language Models at Meta," FSE 2024. https://arxiv.org/abs/2402.09171v1 (checked 2026-10-03)
[^swebench]: "Are 'Solved Issues' in SWE-bench Really Solved Correctly? An Empirical Study," ICSE 2026. Manual inspection confirmed 28.6% of the divergent patches incorrect. https://arxiv.org/html/2503.15223v2 (checked 2026-10-03)
