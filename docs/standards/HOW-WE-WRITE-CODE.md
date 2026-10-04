# How We Write Code

The reader of your code should be able to check it, not simulate it.

Everything here serves that one aim. Code that can be checked can be tested, reviewed, handed to the next engineer and trusted at 2 a.m. Code that has to be simulated hides its defects until production finds them.

This is the overview. The rules for the boundaries you code against live in [How We Architect Systems](ARCHITECTURE.md). The full test discipline lives in [How We Test](TESTING.md). Reviews check the rules below by ID ([How We Review](REVIEW.md)).

---

## Shape

**W1. Write top-down.** Code reads as a sequence of intent. Start with the story a function tells, then step down, one level at a time, until you reach plain logic.

```ts
async function confirmBooking(bookingId: BookingId) {
  const offer = await loadAcceptedOffer(bookingId)
  await holdDates(offer)
  await holdFunds(offer)
  await confirmDates(offer)
  await captureFunds(offer)
  return issueConfirmation(offer)
}
```

A reader learns what happens in six lines, and trusts each name to keep its promise one level down.

**W2. Every function is a unit or a composition. Never both.** A unit decides. Arguments go in; a value or a typed error comes out. Loops, conditionals, parsing and arithmetic live in units. A composition delegates. It names steps and holds no logic. The forbidden third thing interleaves the two, and that is where defects live, because it has no contract to test against. If you can't describe a function as "it decides X" or "it does A, then B," split it.

**W3. One level of abstraction per composition.** A composition never mixes a delegation call with a raw query, a loop or an assignment. A name that braids two decisions — one check over both the headers and the body — is a design smell. Resolve it rather than implement it.

**W4. Names are promises. Use the ratified vocabulary.** The words in the code match the words in the spec and the tickets. A Booking is not a Reservation. A Listing is not a Property. If you need a new word, ratify it. Don't coin it in a variable name.

---

## Decisions

**W5. Decide on primitives.** Branch on the fact itself, not on something derived from it and not on a vendor's dialect. A fallback armed by `startsWith(prefix)` silently never arms for the category member that lacks the prefix. Name the predicate and give it a contract. Keep absent and empty distinct: `value ?? ""` erases a fact.

**W6. Loop by default.** Accept a collection. A single item is a list of one. `getListings(ids)`, not `getListing(id)` called in a loop by every caller. Plural becomes the common case; start there.

**W7. Push dependencies to the edge.** Network, database, clock, randomness and other services live at the module boundary and are passed in. Core logic stays pure. Pure code tests in microseconds, with no mocks and no global state.

---

## Failure

**W8. Units raise. Intermediaries pass. Boundaries catch.**
- A unit that can't keep its contract raises a typed error saying what failed, whether a retry is safe and what is affected. Not a string, not a boolean, not a sentinel.
- A composition doesn't catch. It has no basis to handle an error it didn't cause.
- Each request, job or process boundary catches exactly once, translates to the external error contract, and records a disposition — Recovered, Degraded or Failed — with a signal.

An empty catch is never acceptable.

**W9. Fail closed.** When the code is unsure about authorization, eligibility, integrity or money, it denies. A control missing at startup stops the startup.

---

## Data

**W10. Let the database enforce invariants.** Constraints, exclusion constraints, unique keys and check constraints hold what must never break. Discipline in application code does not. Write only where data differs (`IS DISTINCT FROM`), so a sync that brings nothing new writes nothing. Return values from inserts and updates instead of reading them back.

**W11. Claim; don't check.** Never read a state, decide, then write. Claim the transition in one statement and treat zero rows as "someone else got there first":

```sql
UPDATE booking SET status = $next WHERE id = $id AND status = $expected
```

Attempt the operation and let the authority arbitrate.

**W12. Key every effect.** A call that moves money or causes an external effect carries an idempotency key derived from the entity and the step, stable across retries. A key generated per attempt defeats the purpose.

**W13. Our clock orders.** Order by times we recorded. A timestamp from a client or a producer is evidence of what it claimed, not an ordering.

**W14. Bounds live in config.** Windows, TTLs, quotas, retry counts and ages default to today's values and change without a deploy.

**W15. Append; don't mutate history.** Removals are tombstones. Corrections are new facts that reference the old ones. Overrides are deletable rows.

---

## Change

**W16. Hacks live at seams.** A special case kept for parity is ported exactly, commented with why and with the legacy file and line, and kept at a named seam. The core stays clean.

**W17. Earn every layer.** Before adding code, a dependency, a wrapper or a config layer, stop at the first rung that holds:
1. Does it need to exist?
2. Does the standard library do it?
3. Does the platform?
4. Does an installed dependency?
5. Can the minimum behavior be said more directly?

Only then write it. Never cut validation, security, observability, rollback or audit to get there. Those are structure, not ceremony.

**W18. Comments say why.** The code says what. A comment explains what the code can't: a vendor quirk, a legal constraint, a ceiling and the trigger that lifts it.

---

## Tests

W19 and W20 are the shape a test takes. [How We Test](TESTING.md) is the integrity discipline that makes a suite evidence (T1–T40); where the two meet, TESTING.md governs.

**W19. Tests come from the spec, not the code.** A test written by reading the implementation proves only that the implementation does what it does.

**W20. Test contracts, order and seams.**
- A unit gets contract tests: the nominal case, every error it can be provoked into, and the catch-all.
- A composition gets one test that its steps happen in order with the right arguments.
- Every seam between them gets a test through real units. Defects cluster where two correct units were never run together.
- Every disposition gets a forcing test that asserts both the disposition and its signal. A control that is never fired is a control that isn't there.
- A test that can't fail proves nothing. A test that only proves a fixture proves nothing. Use real payloads.
