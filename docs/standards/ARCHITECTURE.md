# How We Architect Systems

Read this before you design a service, a boundary, an integration or a migration. It states how we decide, why, and what we refuse. It is doctrine, not a catalog of services. A design that violates a rule here is wrong, not merely different. A domain that needs another style may argue for it. It must name the style and the reason.

Say it in a room as: **"Helland-style: authoritative about our own transaction, everything upstream is an input, reconcile the economics in settlement."** That sentence gives a senior engineer the whole shape.

Companions: [How We Write Code](HOW-WE-WRITE-CODE.md) for code, [How We Test](TESTING.md) for evidence, [How We Review](REVIEW.md) for design and code review. Principle IDs (A1, W1, T1) are stable. Reviews cite them.

The worked examples use one illustrative domain throughout: a lodging marketplace that sells its own inventory and inventory held in partners' property-management systems (PMS), through its own checkout and through external channels. The domain is chosen because it has every hard part at once: money, partner systems we do not control, and a guest waiting at checkout. The rules do not depend on it.

---

## The generating rule: one authority per fact

When a design decision is hard, you are almost always missing an authority, not a service. Name the owner and the tangle resolves.

In one marketplace re-platform this rule collapsed seventeen proposed deployables to three, almost none of it by cutting scope. The complexity did not vanish. It moved out of the guest checkout, where it was a bug, into settlement, where it is a business process with an owner.

Know its limit. The rule is a forcing function, not a generator. It decides who owns a fact. It says nothing about what counts as one fact versus two. That first move is domain modeling, and it is done on purpose. A service that uses a value is not a claimant on it. "Pricing calculates price. My calculator doesn't pay my bills." The term of an arrangement and the calculation over it are two facts with two owners.

---

## References, by altitude

**Pat Helland — the individual to name.**
- *Data on the Outside vs. Data on the Inside.* Data inside a service is authoritative and mutable. Data outside is immutable, versioned, and refers to a point in time. A copy you hold is a memory of someone else's answer, never a replacement for it.
- *Memories, Guesses, and Apologies.* Record immutable facts. Act on partial knowledge. Build business mechanisms for the guess that was wrong. Capturing a price delta as a signed fact for settlement is an apology mechanism.
- *Life Beyond Distributed Transactions.* Entities and activities. No distributed transactions. An activity across owners takes tentative actions at each, then confirms. Nothing is undone.
- *Building on Quicksand.* Reconciliation is first-class design, not cleanup.

**Eric Evans, *Domain-Driven Design* — the method.** Bounded contexts with one authority per fact. A ubiquitous language: ratified vocabulary that code, tickets and docs use, and nothing else. An anti-corruption layer at every external boundary. The rare part is enforcing the authority rather than stopping at the vocabulary.

**Rich Hickey, *Simple Made Easy* — the philosophy.** Simple is objective: one thing, unbraided. Easy is subjective: familiar. Complecting is braiding concerns together. Complexity is tangle, not part count. A model with more named concepts and fewer entangled ones is the simpler model.

**Martin Fowler — the practitioner's toolkit, especially for change.**
- *What do you mean by "Event-Driven"?* Event notification, event-carried state transfer, event sourcing and CQRS are four different things. We use event notification. We do not event-source and do not run CQRS. A flow such as guest pays, payment authorizes, booking confirms is a command flow. Dressing it as events is a passive-aggressive command.
- *Bounded Context*, *Monolith First*, *Microservice Prerequisites.* Domain decomposition is not deployment decomposition. "Services (I hate microservices) that are specific, well-scoped, do their one thing, and function as black boxes."
- *Strangler Fig Application.* A replacement grows around the old system and takes over its behavior. The old code is deleted only once provably unreferenced. No big-bang cutovers.
- *Branch by Abstraction.* The consumer codes against the owner's interface. Until the owner ships, a legacy source sits behind that interface. Nothing else knows.
- *Parallel Change.* Canonical-struct changes ship as a versioned API behind a flag. Data migrations go shadow-write, read migration, write cutover, legacy archive.

---

## Principles

### Ownership and authority

**A1. One owner per fact.** No service promotes a copied field to authority by convenience. *Prevents:* two services owning price; a cache that quietly becomes the record.

**A2. Each service owns its data and its database.** One writer. No shared schema. No cross-service table reads. Others read through the owner's API.

**A3. A service does one thing.** "Do not build a reservation system inside a distribution service." Every spec carries a *Does not own* list: what the service will be asked for and must refuse.

**A4. Put facts with the thing they describe, not with today's only consumer.** Facts about a property belong to the property service, even when one consumer needs them. Facts about a pair belong to the owner of the pair. Two facts that share a noun are still two facts: whether a property participates in a marketplace program is the marketplace's fact; the markup, minimum rate and listing identity for that property on an external channel are distribution's.

**A5. A copy is a memory, never an authority.** Share what others need. Hold another owner's fact only as an immutable, versioned, point-in-time copy that knows its own age: a snapshot, a cache, a projection. It never settles money and never decides a booking. *Examples:* the resolution snapshot a booking takes at checkout; the payload a search index is fed.

**A6. The binding decides the system of record.** Authority binds per property and per fact class. Stay and price bind separately; a property is often priced by the platform and held in a partner's PMS at once. Only the system of record accepts a reservation. A channel's confirmation, such as an instant-book acknowledgement, is not authority. "The only point of control is the System of Record."

**A7. Supply and demand are separate.** Booking (guest, demand) is not Reservation (host, supply). The stay and the payment cross that line, and each is reconciled explicitly.

### Boundaries and deployables

**A8. Map boundaries first,** against the owning services' specs, before any design. The accountable human signs scope. Never self-ratify it.

**A9. Domain decomposition is not deployment decomposition.** Naming a bounded context is free. Running a service costs an on-call rotation, a database, a contract and a release train. Build bounded contexts as modules. Extract a deployable only for measured scale, a distinct team, an isolation requirement or proven contention — "never because it felt like a separate thing." *Example:* disbursement, tax, compliance and a trust ledger can ship inside one payment deployable: one domain, one reconciliation surface, one team. Their separation is a schema and a write path, not a deployable.

**A10. References point inward.** A core service never carries an identifier from a layer above it. The property service has no `site_id`.

**A11. Each layer is minimal.** Only what it must be. Nothing borrowed from above or below. Pagination and screen shaping belong to the API layer.

### Abstraction

An abstraction is the most leveraged decision in a design. Get it right and variation stays where it is born. Get it wrong and every caller pays for it, forever.

**A12. An abstraction is a promise stated in the caller's vocabulary.** Its operation set is what callers need, not the union of what implementations offer. *Example:* a PMS-integration layer's operations are ours, not the vendors'. It composes vendor calls to satisfy them.

**A13. The abstraction absorbs variance. Callers never branch.** Not on source of truth, vendor, implementation or capability. When an implementation cannot do something, the abstraction decides the canonical answer once, inside, and records why. No capability flag leaks upward. *Examples:* `hold` returns true, as a no-op, for a vendor with no hold. A vendor that cannot change dates atomically gets `changeDates → false` rather than risk losing the stay. The integration layer alone resolves the source-of-truth binding; for a property held in our own system it forwards with no mapping. The extra hop is worth it.

**A14. Test a boundary by change.** If an implementation gains or loses a feature and a caller has to change, the boundary is in the wrong place. Put variance in the layer that owns it. *Example:* encoding "most vendors can't hold" in the booking state machine solves an integration problem in the wrong layer, and denies your own inventory the real hold it has.

**A15. Abstract the hard part.** An abstraction that absorbs the repetitive cases and leaves every hard case written around it is a cost, not a tool. *Example:* a mapping DSL that absorbs field renaming, and leaves every real difference between vendors in hand-written adapters, is machinery larger than the problem it solved.

**A16. One contract, many implementations, one test.** Our own implementation is just another one. The contract test runs against every implementation, or the implementations drift quietly. *Example:* our own reservation system answers a quote in the same shape a vendor does, behind the same integration contract.

**A17. Choose the representation that makes the operation simple.** Lift the complexity into the data, not the algorithm. *Example:* pricing carries `guests × nights` as its own vector component, so a per-guest-per-night fee is one coefficient and evaluation stays linear.

**A18. A simplification that breaks a domain invariant is wrong, however elegant.** *Example:* collapsing the fee matrix into one polynomial is mathematically sound and wrong, because rounding does not distribute and an invoice must itemize to its total.

**A19. Change an abstraction through its inputs, never by adjusting its outputs.** A post-processing step outside the abstraction is a second, hidden abstraction. *Example:* markups and discounts are inputs to the pricing engine, never adjustments to its result.

**A20. An interface needs a boundary or a variance to justify it.** An owner's contract qualifies. A strangler seam qualifies, even with one implementation. A wrapper with one implementation and neither is ceremony. Delete it.

### Integration and contracts

**A21. Use the real paths.** "Don't imagine bullshit and don't make up how it works." Integrate with the APIs that exist today. A consumer never hands an owner a list of changes; you would not do that to your payment processor. Where an owner does not yet expose a fact, bridge it behind the owner's interface (A49) and record the gap.

**A22. One canonical write path per entity, called by everyone.** `property.updateProperty(canonical)`, never `updateFromSourceA(odd_struct)` "and a dozen other minorly different things. Nope. Simple. Uniform." Origin is provenance on the write, not a different door.

**A23. Serve whole canonical objects. Shape in the API layer.** A domain service that changes because a screen changed is a recipe for disaster. Keep objects small enough that whole-object fetch and wholesale cache invalidation are the obvious choice.

**A24. Anti-corruption layer at every external boundary.** Connectors carry transport. Adapters translate vendor dialects into the canonical struct. Vendor quirks never pass the adapter.

**A25. HTTP status describes the protocol, never the service's meaning.** "You don't know if a 404 is a poorly formed URL or a missing object." Services say what happened in the body, as a closed set of error kinds with retry-safety declared. The status may mirror the kind. Callers branch only on the kind.

**A26. OpenAPI before behavior.** The spec is written as the service is built. It is authoritative over contracts, tests, fixtures and generated clients.

### Transactions and consistency

**A27. Synchronous by default.** Request/response is the norm. Queues exist only for durable background work. The guest is at checkout with a card out. "The event log is a log, not a bus."

**A28. No distributed transactions. A flow across owners is a two-phase workflow, not a saga.** Prepare takes revocable holds at each owner. Confirm makes them real. A saga commits each step and undoes committed steps afterward; we never do that. A failure before confirm releases holds, so nothing needs undoing. The one failure after confirm is an apology: a business process, reconciled in settlement. No transaction coordinator. No compensation framework.

**A29. Order steps so every failure is benign.** Quote, which checks availability and price in one call; split them and they race. Hold dates. Hold funds. Confirm dates. Capture. Issue confirmation. The only loose end is a confirmed stay with no capture, and it reconciles. A captured charge against unconfirmed dates is unreachable.

**A30. Caches narrow; authorities decide.** Availability is an advisory cache. Never check-then-act. Attempt the operation and let the authority arbitrate at write time, as an ATM does against the bank. If the cache dies we are slow, not wrong.

**A31. Protected uncertainty denies.** Fail closed on authorization, eligibility, compliance and money. An unknown is a no.

**A32. Idempotency follows the effect.** Every call that moves money or causes an external effect carries a deterministic key derived from the entity and the step, and the receiver deduplicates durably. A key generated per attempt defeats the mechanism. Operations idempotent by construction check on conflict instead, so the fast path pays nothing.

### Events and time

**A33. Observations are lossy; obligations are committed.** Two kinds of event, two guarantees. An observation is fire-and-forget; losing one delays state and never breaks correctness. An obligation — an audit record, or the intent to cause an external effect — commits in the owner's own transaction as an outbox row and is dispatched idempotently. Nothing that must happen waits on a notification.

**A34. Owners emit at their own milestones, never from calling code.** No consumer registrations live inside an owner. *Prevents:* dozens of caller-side "availability changed" calls scattered through a monolith, each one a place the next change forgets.

**A35. A command is a command.** A flow the caller waits on is a request. Do not hide it in events.

**A36. Story builders assemble and route. Owners act.** Story builders turn events into stories and route them to an owner. They trigger nothing themselves. Side effects stay sequestered with the owner.

**A37. Reconciliation lives outside the service that does the work.** Prioritize what is most likely to matter: for availability, the dates most likely to be booked, not the nearest.

**A38. Our clock orders events. A producer's timestamp is evidence.** Record receipt time from our clock. Keep the producer's time as what it claimed.

### Money

**A39. Be authoritative about your own transaction, not others' intent.** We cannot say what a partner system intended a price to be. We can say exactly what we offered and what we charged. The price we show is the price we charge. A changed price is a requote the guest accepts.

**A40. External feeds are inputs, never history.** A feed that revises itself does not rewrite a booking or a payment.

**A41. Never demand perfect agreement between systems you do not both control.** "Two systems cannot reliably be in 100% alignment 100% of the time." The demand is the defect. Agree a tolerance. Record the delta as a signed fact. Reconcile in settlement. A nickel per booking is not worth rigidity.

**A42. Price is computed in exactly one place.**

**A43. Every money line carries a funder and a recipient.** Every payment rule declares its basis — share of charge or declared rate — before the two numbers can diverge.

### Failure and retries

**A44. Send state, not deltas.** On failure keep one pending record per entity, not per change. On reconnect send the latest state. Only a channel that truly needs every change gets a log.

**A45. Our side broken: delist. The partner's side broken: retry.** Retry with backoff inside the partner's quota. When the whole partner is down, keep the records and retry. Each partner fails independently.

**A46. Recovery is not silence.** Every error site declares a disposition — Recovered, Degraded or Failed — and every disposition emits a signal. An outage is one signal per partner, not one per retry. The operate-phase form of this rule is [Reliability as Signal](../practices/reliability-as-signal.md); what happens once a signal fires is [Graduated Incident Response](../practices/incident-response.md).

### Replacing a system

**A47. A replacement fully replaces and keeps the better architecture.** "You're a failure if it doesn't replace the existing service. You've failed as well if the architecture sucks." Parity is measured by tests that show the new path emits what the old one did.

**A48. Carry hacks exactly, at named seams.** A special case kept for parity is ported exactly, commented with why and the legacy file and line, and lives only at a named seam. The core stays ideal. Compatibility code has an owner and a removal path.

**A49. "No owner exposes X" is never a reason to drop X.** Put a read-only legacy source behind the owner's interface, one method per fact, commented with which owner will expose it. Delete it when the owner ships.

**A50. Parity means behavior, not code.** The legacy inventory is a map of the concerns the domain contains, not code to preserve. Where the legacy model is defunct, build greenfield. "A strangler which preserved the crap" is not a strangler.

### Data lifecycle

**A51. Nothing is hard-deleted except ephemeral state.** Removals are tombstones. Historical facts are frozen. Corrections are new facts appended beside the old; a payout adjustment lands in the next cycle.

**A52. Overrides are records, not values.** An override is a deletable row with a scope and a match rule. Deleting it restores inheritance with no restore step.

**A53. Ephemeral data is configured by type.** A TTL and key identifiers per kind. Content-addressable IDs give deduplication and idempotency for free.

### Operability

**A54. Measure freshness; do not target it blindly.** Report it as a metric. "If we can make it faster, we do."

**A55. Every bound lives in config.** Windows, horizons, quotas, pacing, ages and retention, defaulting to today's values. Never hard-code a bound.

**A56. Keep raw signal and applied correction as separate facts.** Separate systematic error from idiosyncratic, and escalate the systematic. A learned correction that hides the defect it measures is the O-ring trap.

**A57. Verify against reality, not proxies.** Real bundles, real payloads, real deployables. A test that only proves a fixture proves nothing.

---

## Anti-patterns we reject

- **God services.** A distribution service that holds bookings, validates content or decides price.
- **Microservices for their own sake.** Seventeen deployables because each "felt like a separate thing."
- **Callers branching** on source of truth, vendor, implementation or capability. "Then every service has to have a switch."
- **Variance in the wrong layer.** A vendor limitation encoded in a caller's state machine.
- **Abstractions over the easy part.** Machinery that absorbs the repetitive cases and leaves the hard ones outside it.
- **Adjusting an abstraction's output.** A markup applied after the price engine returns.
- **Ceremony interfaces.** One implementation, no boundary, no seam.
- **Side doors for writes.** `updateFromA`, `updateFromB`, `internalUpdateFromSomeSourceA`.
- **Copies promoted to authority.** A cache or snapshot used as the record.
- **HTTP status as meaning.** Branching business logic on a 404 or a 500.
- **Sagas.** Committing steps across owners and undoing them afterward, where a two-phase workflow with revocable holds does the job.
- **Async for a flow a guest waits on.** Queues, orchestration, or events used as passive-aggressive commands.
- **Obligations on the lossy path.** An audit record or an external effect that depends on a notification arriving.
- **Guaranteed delivery bolted onto observations** that must reconcile anyway.
- **Events emitted from calling code.**
- **A consumer demanding owner changes,** or inventing APIs for other services to build.
- **Imagined integrations.** Designing against how you assume an API works.
- **Dropping behavior during a re-platform,** or keeping parity by bending the core instead of isolating the hack at a seam.
- **Demanding zero divergence** from systems we do not control.
- **Hard deletes and mutated history.**
- **Pattern-matching on existing code.** "Existing code and 'the right thing' are often divorced." Reason from first principles.
- **Reviewing ownership by diffing summary lists.** An *Owns* list is a summary. The design is the prose.

---

## Checklist before you design

1. What are the facts, and which are one fact versus two? Who owns each? Write the *Owns* and *Does not own* lists.
2. Which bounded contexts exist? Which need a deployable, and what is the measured reason?
3. How does each entity bind for each fact class? Does any caller need to know? None should.
4. What is each abstraction's operation set, in the caller's words? Where does each variance live? If an implementation changes, which callers change? None should.
5. What real APIs exist today? Have you read them? Where is a fact not yet exposed, and what legacy source stands behind the owner's interface?
6. Is there exactly one canonical write path per entity?
7. Where is the flow a command and where a notification? Is it synchronous unless there is a reason?
8. Which steps are tentative and which confirm? Is every failure benign? What is the one loose end, and who reconciles it?
9. Which events are observations and which are obligations? If every observation is lost, what is delayed and what breaks? Nothing should break.
10. On failure, what state is pending, per what key, and what is sent on recovery? Whose side is broken: delist, retry or suspend?
11. For a replacement: where is the parity inventory, where are the parity tests, and at which named seams do the hacks live?
12. What is deleted, what is tombstoned, and what is ephemeral with a configured TTL?
13. Which bounds are in config? Which signal does each disposition emit? What is measured rather than targeted?
14. Which tradition does this answer to? If not this one, why?

---

## How the factory uses this document

This is the standard the Architecture phase ([SOFTWARE-FACTORY.md §4, Phase 2](../SOFTWARE-FACTORY.md)) answers to. The Architecture Specification declares its style — this one by default, or another named with its reason (item 14 above) — and design review checks the specification against the A-rules by ID ([How We Review](REVIEW.md), passes D2–D6). A violation found in review is a finding with a rule ID, not a matter of taste.

## Provenance

Distilled from architecture guidance, design reviews and *Does not own* tables written for marketplace and partner-integration re-platform work (2025–2026), and generalized for the factory. Examples were rewritten into one illustrative domain; no consuming target's internals are described here.
