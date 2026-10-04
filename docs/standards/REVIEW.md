# How We Review

A review is a series of narrow passes. Each pass looks for one class of violation and ignores everything else.

Broad reviews fail in a predictable way. Attention spreads across everything, the easy findings fill the page, and the structural defect goes unseen: the copied field used as authority, the swallowed error on the money path. Nobody was looking only for it. A narrow pass can't miss that way. It has one question, a short list of what a violation looks like, and permission to ignore the rest. Passes are cheap, run in parallel, and each one can be checked.

Rules are cited by ID: A# from [How We Architect Systems](ARCHITECTURE.md), W# from [How We Write Code](HOW-WE-WRITE-CODE.md), T# from [How We Test](TESTING.md).

This is the review method. The binding rules for an *agent* reviewer — ratified intent from trusted inputs, risk class separate from test outcome, what an agent may and may not approve — are [`prompts/code-review.md`](../../prompts/code-review.md). A change to intent, policy or a protected boundary also goes through the [Diff-Intent Gate](../../prompts/diff-intent-gate.md).

---

## How a pass runs

1. **One question.** Read the change looking only for this pass's violations. Anything else you notice gets one line, tagged with the pass that owns it. Don't chase it.
2. **Read the owner, not just the diff.** Authority and boundary findings need the owning service's spec. A diff alone hides what the change reaches.
3. **Every finding cites.** Location (`file:line` or document section), rule ID, what the code does, what it should do, severity.
4. **Try to refute before you report.** Read the surrounding code, the owner's spec and the tests. A finding that survives goes in. One that doesn't is recorded as refuted, with the reason, so the next reviewer doesn't raise it again.
5. **Clean is a finding.** Record what you checked. "C2 clean: 14 files, no writes outside Booking's schema, no reads of another service's tables" can be wrong. "LGTM" can't.
6. **The reviewer does not fix.** The author fixes. The pass reruns on the new diff.
7. **One reviewer per pass,** human or agent. An agent reviewer gets a fresh context holding only its pass, the change and the owners' specs. Passes run in parallel. Frame runs first, because it can void the rest.

---

## Severity and verdict

| Severity | Meaning | Effect |
|---|---|---|
| **Blocker** | Wrong money, broken authority, security or privacy exposure, fail-open, lost or corrupted data, invalid evidence | Blocks merge |
| **Major** | Will cause a defect or drift: a second write path, a caller branch, an untested seam | Fix before merge, or a dated exception with a named owner |
| **Minor** | Clarity, naming, comments | Author's call |

The verdict is **Merge**, **Merge with dated exceptions** or **Block**. Any unrefuted Blocker blocks. A required pass that was skipped is a gap, recorded with its reason. It is not a clean pass.

---

## Which passes run

**Design review** (spec, ADR, brief): D1–D4 always. D5 when money moves. D6 when something is replaced or migrated. D7 always, last.

**Code review:**

| The change… | Required passes |
|---|---|
| any code | C1, C3, C4, C9, C10 |
| touches another service, an API, a schema or a canonical struct | + C2 |
| touches money, booking, availability or a state transition | + C5 |
| emits or consumes events, or causes an external effect | + C6 |
| touches auth, personal data, tokens or untrusted input | + C7 |
| adds config, retention, a migration or a legacy seam | + C8 |

---

## Design passes

### D1 — Frame
**Question:** Is this the right problem, and does the document answer it?

**Look for:**
- An answer to a question nobody asked, or the question that was asked left unanswered.
- A load-bearing premise that is false or unverified.
- A recommendation with no alternative named, including doing nothing.
- An unstated cost: money, latency, headcount, reversibility.
- A second-order effect nobody traced: an incentive created, load moved elsewhere.

**Ignore:** structure, naming, style.

### D2 — Authority (A1–A7)
**Question:** Does every fact have exactly one named owner?

**Look for:**
- A fact with two claimants, or with none.
- A service without a *Does not own* list, or one that takes a fact from that list anyway.
- A copy, cache or snapshot used to decide or to settle.
- A fact placed with its current consumer instead of with the thing it describes.
- System of record stated per property or per mode, instead of per fact class.
- Demand and supply merged: Booking doing Reservation's job, or the reverse.

**Ignore:** deployment topology, performance.

### D3 — Boundaries and abstraction (A8–A26)
**Question:** Does each boundary absorb its variance and speak its callers' language?

**Look for:**
- An operation set copied from a vendor rather than defined by callers.
- A caller that branches on source of truth, vendor, implementation or capability.
- One implementation's limitation encoded in a caller.
- An abstraction that handles the easy cases and leaves the hard ones outside it.
- A step that adjusts an abstraction's output after it returns.
- An interface with one implementation and no boundary or seam.
- A new deployable without a measured reason.
- A reference pointing outward from a core service.
- More than one write path for an entity.
- Screen shaping inside a domain service.

**Ignore:** step ordering, money.

### D4 — Consistency and failure (A27–A38, A44–A46)
**Question:** When any step fails, is the result benign and visible?

**Look for:**
- A committed step that must later be undone: a saga where a two-phase workflow belongs.
- Steps ordered so that money can be captured against something unconfirmed.
- Availability and price checked in separate calls.
- Check-then-act, or a decision taken from a cache.
- An obligation (an audit record or an external effect) on the lossy notification path.
- A flow the guest waits on routed through queues or events.
- An effect without a deterministic idempotency key.
- Uncertainty resolved to "allow."
- A failure with no disposition, no signal, or no delist/retry/suspend answer.

**Ignore:** naming, deployables.

### D5 — Money (A39–A43)
**Question:** Can every cent be traced to one computation, one funder and one recipient?

**Look for:**
- Price computed or adjusted outside the pricing engine.
- A money line without a funder and a recipient; a payment rule without a basis.
- An external feed written into transaction history.
- A demand for exact agreement with a system we don't control, with no tolerance and no delta record.
- A past transaction re-derived from current state instead of its snapshot.

**Ignore:** everything that isn't money.

### D6 — Change and migration (A47–A51)
**Question:** Can this roll out, and roll back, without a big bang?

**Look for:**
- A cutover with no shadow or parallel-run stage.
- A replacement without a parity inventory or parity tests.
- Behavior dropped because no owner exposes it yet.
- Hacks outside named seams, or the core bent to keep parity.
- Legacy structure preserved where only legacy behavior needed preserving.
- A destructive step without a rollback; hard deletes.

**Ignore:** the internals of the target design.

### D7 — Evidence
**Question:** Is every consequential claim sourced or reasoned, and could it be wrong?

**Look for:**
- A factual claim with no source; a conclusion with no mechanism.
- Measured, inferred and assumed not distinguished.
- No stated condition under which the conclusion would be wrong.
- "Implemented," "done" or "verified" without an artifact.
- Agreeing sources stacked as strength, with no severe test.

**Ignore:** the design's merits. D1–D6 own those.

---

## Code passes

### C1 — Scope and language (W4)
**Question:** Does the change do what was asked, all of it, and nothing else?

**Look for:**
- Behavior not in the ticket or spec; required behavior missing.
- Unrelated refactors riding along.
- Names outside the ratified vocabulary; one concept under two names.

**Ignore:** how well the code does it.

### C2 — Authority and boundaries (A1–A5, A10, A12–A13, A22–A25)
**Question:** Does this code touch only what its service owns, through the owners' contracts?

**Look for:**
- Writes to another service's data; reads of another service's tables.
- A second write path for an entity.
- A copied field or cached value used to decide.
- An identifier from a higher layer carried into a core service.
- Vendor field names or quirks past the adapter.
- A branch on source of truth, vendor, implementation or capability.
- Business logic keyed on HTTP status.

**Ignore:** internal structure.

### C3 — Structure and abstraction (W1–W7, A17–A20)
**Question:** Can each function be checked rather than simulated?

**Look for:**
- A function that both delegates and decides.
- A composition mixing levels: delegation beside a query, a loop or an assignment.
- A branch on a derived value or a dialect; absent collapsed into empty.
- A single-item API where callers will loop.
- Network, database, clock or randomness inside core logic.
- A wrapper, interface or layer with no boundary and no second implementation.
- An abstraction's output adjusted after the fact.

**Ignore:** error handling, tests.

### C4 — Errors and failure (W8–W9, A31, A46)
**Question:** Does every failure surface exactly once, typed, with a disposition?

**Look for:**
- An empty catch. A catch inside a composition.
- Errors as strings, booleans, sentinels or nulls.
- A boundary that catches without translating, or without emitting a disposition and signal.
- Uncertainty resolved to "allow"; a missing control logged and ignored.
- A retry of something not declared retry-safe.
- Errors mapped by HTTP status instead of by kind.

**Ignore:** happy-path logic.

### C5 — Consistency and money (W10–W13, W15, A28–A30, A39–A43)
**Question:** Is every state change claimed atomically, and every cent accounted for?

**Look for:**
- Check-then-act; a read-decide-write without compare-and-set.
- A cache or projection deciding a booking or a charge.
- An invariant held by code that the database could hold.
- An update that writes when nothing changed.
- Idempotency keys missing, or generated per attempt.
- A step order that can capture against unconfirmed dates.
- Price arithmetic outside the pricing engine; a money line without a funder and a recipient.
- History mutated; a hard delete of non-ephemeral data.
- Client or producer time used for ordering.

**Ignore:** naming, structure.

### C6 — Events and effects (A33–A36)
**Question:** Is every obligation committed, and every notification harmless to lose?

**Look for:**
- An external effect or audit record not committed as intent in the same transaction.
- Code that assumes an event arrives, arrives once, or arrives in order.
- An event emitted by a caller rather than by the owner.
- A command dressed as an event.
- A consumer registration inside an owning service.
- A story builder that acts instead of routing.

**Ignore:** event payload style.

### C7 — Security and privacy (A31)
**Question:** Can anyone reach data or actions they shouldn't, or can anything leak?

**Look for:**
- A protected path without an authorization check, or one that defaults to allow.
- A query without its tenant, realm or partition scope.
- An inbound transformation that widens scope: a default organization, an expanded shorthand.
- Personal data, secrets or tokens in logs, events, errors, metric labels or URLs.
- Raw tokens or keys stored instead of digests.
- Untrusted input reaching a query, template, path or shell unvalidated.

**Ignore:** everything that isn't access or leakage.

### C8 — Configuration and lifecycle (W14–W16, A48, A51–A53, A55)
**Question:** Will this still behave when the numbers change and the data ages?

**Look for:**
- A hard-coded window, TTL, quota, retry count or age.
- Ephemeral data without a TTL; durable data without a retention rule.
- Overrides stored as overwritten values.
- A migration without a rollback, or without expand-and-contract steps.
- A parity hack outside a named seam, or without its legacy file and line.

**Ignore:** business logic.

### C9 — Tests (W19–W20, T1–T40, A57)
**Question:** Would these tests fail if the code were wrong?

**Look for:**
- Tests that mirror the implementation instead of the spec; an expected value with no outside oracle (T1).
- A test edited in a change set that cites no superseding signed item (T3).
- A unit without contract tests: nominal, each provokable error, the catch-all.
- A composition without an order test; a seam without a test through real units.
- A disposition or signal never forced by a test.
- Mocks of the thing under test; patching instead of a declared seam (T16); fixtures where real payloads exist.
- A test with no named falsifier, or a Critical invariant with no semantic falsifier (T31).
- A blanket mask or a regex in a test body where a normalizer belongs (T29); a golden file nobody ratified (T30).
- A retry, sleep or tolerance window holding a test green (T33).
- Assertions that cannot fail.

**Ignore:** the code under test, except to read what it claims.

### C10 — Evidence and docs (A26)
**Question:** Is everything the change claims about itself true and checkable?

**Look for:**
- A description claiming "tested," "verified" or "done" without a run, a link or a `file:line`.
- An API change without the OpenAPI spec updated first.
- Docs, ADRs or generated clients that no longer match the code.
- A partial change presented as complete; a known gap left unnamed.

**Ignore:** the quality of the change itself.

---

## Recording a pass

```
Pass: C5 — Consistency and money
Scope checked: booking/confirm.ts, booking/transitions.ts, payment client calls (6 sites)

Findings:
  [Blocker] booking/transitions.ts:88 — A30, W11
    Reads status, then updates by id. Two advancers can both proceed.
    Fix: UPDATE … WHERE status = $expected; treat zero rows as a lost claim.
  [Major] booking/confirm.ts:41 — A32, W12
    Idempotency key is randomUUID() per call; a retry charges twice.
    Fix: derive the key from (bookingId, "capture").

Refuted:
  booking/confirm.ts:57 — suspected cache read before charge.
    It reads the quote snapshot, not the cache. A5 permits.

For another pass:
  C6 — booking/confirm.ts:112 emits booking.confirmed from the caller.

Verdict for this pass: Block
```
