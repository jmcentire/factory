# The Software Factory

### How we build and how we repair

**Three roles. Three phases. Humans own intent, architecture, and risk — the factory drafts,
implements, proves conformance, and produces the evidence.**

---

## Status of this document

This document specifies the system. It does not report the system.

Deployment status — which controls are wired and enforcing today and which are design only —
is tracked in the operational guide, and a reader deciding whether to rely on a given control
must check there. **A control that is specified here is not a control that is running.** A
boundary described in the present tense is a boundary the design intends to enforce, which is
not a claim that it enforces now.

For this repository, the operational guide is the
[`README.md` doctrine-to-code mapping](../README.md#doctrine--code-mapping). It is the
authoritative statement of what `factory_core` and `factory_runtime` currently enforce and
what remains design only.

The discipline the factory imposes on the software it builds — that nothing is marked done on
the strength of a description — applies to the factory's own description of itself.

---

## Contents

**Part I — The System**

1. [What this is, and why](#1-what-this-is-and-why)
2. [What already exists, and what is missing from it](#2-what-already-exists-and-what-is-missing-from-it)
3. [The three roles](#3-the-three-roles)
3.5. [Criticality](#35-criticality)
4. [The three phases](#4-the-three-phases)
4.5. [Invariant documents](#45-invariant-documents)
5. [Translation boundaries](#5-translation-boundaries)
6. [What is shared and what is independent](#6-what-is-shared-and-what-is-independent)
7. [The eight non-negotiables](#7-the-eight-non-negotiables)
8. [Two flows, one structure](#8-two-flows-one-structure)
9. [The environment ladder](#9-the-environment-ladder)
10. [The build loop](#10-the-build-loop)
11. [The gate](#11-the-gate)
12. [The evidence plane](#12-the-evidence-plane)
13. [The factory is itself a regulated system](#13-the-factory-is-itself-a-regulated-system)
14. [What the factory cannot reach](#14-what-the-factory-cannot-reach)
15. [What changes for engineers](#15-what-changes-for-engineers)
16. [The core guarantee](#16-the-core-guarantee)

**Part II — Role Directives**

- [Shared foundation](#shared-foundation)
- [Directive: Validator](#directive--validator)
- [Directive: Coder](#directive--coder)
- [Directive: Tester](#directive--tester)

**Appendices**

- [Appendix A — Phase and role map](#appendix-a--phase-and-role-map)
- [Appendix B — Changelog](#appendix-b--changelog)

---

# Part I — The System

## 1. What this is, and why

We are moving from a model where engineers spend most of their throughput writing code to a
model where engineers design and operate a system whose output is the desired code or the
desired fix. The engineer's center of gravity shifts toward the specification, the
architecture, the invariant, the diagnosis, and the risk decision.

This is a restructuring of where judgment lives, not a removal of it.

| Humans are good at | Machines are good at |
|---|---|
| Judgment about intent — whether the right problem is being solved | Exhaustive, tireless conformance checking against a fixed target |
| Recognizing a described behavior as wrong | Generating candidate behaviors, edge cases, and contradictions at volume |
| Architecture and the tradeoffs with no clean answer | Drafting an architecture and stating its consequences for review |
| Diagnosing why a system fails | Executing the ten-thousandth check as carefully as the first |
| Deciding whether a risk is acceptable | Refusing to be tired, rushed, or fond of its own prior work |

The premise that makes this safe rather than reckless is a single observation about how
engineering processes fail.

> **The default failure mode of any process, human or machine, is premature and unverified
> confidence.** Someone declares a thing done and the declaration is accepted as evidence. It
> is not evidence. It is a hypothesis.

The entire factory is built to refuse that hypothesis until it has survived an attempt to
refute it. This matters more when the implementer is an agent, because an agent that misreads a
specification produces confident, well-formatted, plausible work that is wrong — and a second
agent sharing the same misreading will cheerfully confirm it.

**The honest version is stronger than the inflated one.** The factory does not guarantee
correctness. It produces independently verifiable evidence, it makes important failures harder
to hide, and it materially lowers the probability of undetected error. Where this document
calls evidence trustworthy it means tamper-evident, independently verifiable, and rooted in
defined trust authorities — never unfakeable, because no evidence chain protects against
compromise of the thing that verifies it.

---

## 2. What already exists, and what is missing from it

Elite engineering organizations already hold most of the pieces this document assembles. They
hold them as cultural agreements and fragmented tooling rather than as a system, and the gaps
between the pieces are where the failures live.

| Piece | How it exists today | Where it fails |
|---|---|---|
| **The spec-and-build split** | The RFC, the architecture review board | The design is written, debated, approved — then the document dies. The author writes whatever they write; a reviewer verifies against a fuzzy memory during a rushed pull request. The agreement was real; the enforcement was not. |
| **The oracle problem** | Consumer-driven contracts, typed interfaces, gRPC and Protobuf | Catches interface drift. But humans still write both the implementation and the test that judges it, reproducing the exact correlated misreading this factory exists to prevent: a bug verified by a test written to match the bug. |
| **Verification** | DevSecOps pipelines, static analysis, dependency alerts | Engineers are blasted with thousands of findings and cope by ignoring them — configuring rules to bypass warnings and clicking merge because the wall of data is too dense to parse. |

What the factory contributes is not a new idea in any one of these places. It is the unification:
an enforced state machine where **the design cannot die in a document** because the build is
generated against it, where **the oracle cannot be written to match the bug** because the party
writing the tests cannot see the implementation and cannot talk to the party writing it, and
where **the human is shown a calibrated decision rather than a wall of logs.**

---

## 3. The three roles

There are three roles. There are no others. Everything else in this document is a phase, an
artifact, or a control — not a role.

| Role | Owns |
|---|---|
| **Validator** | Coordinates with the human. Holds the context. Co-authors the spec. All questions route here. Runs the tests once code and tests are both complete. Judges. |
| **Coder** | Implements against the spec. |
| **Tester** | Writes tests against the spec. |

### The two rules that make this work

**There is no communication between Coder and Tester.** Every question goes to the Validator,
and the Validator answers from the spec or escalates to the human. This is the entire
independence mechanism, and it is structural rather than contractual — not two agents agreeing
not to peek, but two agents with no channel.

**Both read the same spec.** The interface, the schema, the behavior, and the acceptance
criteria are one artifact both consume. If you tell two parties to build "something" and then
expect the tests to pass, you have specified nothing and the passing tests mean nothing.
**Shared spec, no shared channel** is the whole shape.

### The Validator judges against a spec it helped write

**This is a real limitation rather than a clean separation.** It is accepted because the
alternative — a second agent holding the human relationship — reintroduces the translation
boundary it was meant to remove, and adds one.

Three things bound it. The human signs the spec, so the artifact being judged against carries
human authority rather than the Validator's. The behavior ledger converts the spec into concrete
behaviors the human recognized as right, which is a check on the translation that does not run
through the Validator's judgment. And the verbatim-and-ratify rule means the transformation was
reviewed against its source, not accepted on its own coherence.

None of the three catches a mistranslation the human also failed to recognize. That case is
unreached, it is stated in §14, and it is the reason the ledger is load-bearing rather than a
nicety.

> Concretely: a Validator that mistranslated the human's intent in phase 1 will judge
> conformance to its own mistranslation and find it satisfied, and every mechanism downstream
> confirms it. This is the §5 translation-boundary problem, and the front gate does not catch it
> because the front gate is the same party. Naming it is the point — an unnamed
> self-referentiality is the shape §14 spends its length warning about.

### Why the Validator runs the tests

Neither the Coder nor the Tester executes the other's artifact. The Coder cannot run the tests
it is judged by; the Tester cannot run the implementation to discover what it happens to do
and shape assertions around it. Execution belongs to the party that holds neither pen.

### Test level

**During the loop, tests are integration level** — acceptance and feature tests through the
real interface. Unit tests are written *after* the work is coded, tested, and validated.

This ordering is deliberate. Unit tests written before the implementation shape has settled
encode the implementation rather than the specification, and once written they resist the
shape changing. Integration tests assert what the spec promised, which is the thing that must
not move.

---

## 3.5. Criticality

Verification depth keys on oracle adequacy. **What happens when the oracle is inadequate keys
on criticality**, and the two are independent.

Oracle adequacy answers *is this change verified*. Criticality answers *what the factory does
when it is not*, and *how deterministic the evidence has to be before it counts*. A change with
a comprehensive oracle promotes regardless of the surface it touches. A change with a gap in
its oracle is disposed of according to what that surface is for.

Criticality is a property of the **surface**, not of the change and not of the diff. It is
assigned by a human during design formalization, recorded per component in the control profile,
and inherited by every change that disturbs the component.

| Class | Surfaces | Evidence requirement | Disposition when the oracle is silent on a disturbed surface |
|---|---|---|---|
| **Critical** | Authorization and identity · money movement · data integrity · privacy, retention, deletion · safety decisions · irreversible or legally consequential effects · required transactional audit · cryptography · destructive migrations · the factory's own control plane | Complete for every disturbed surface, deterministic, live-verified | **Block. No waiver, no expiring risk acceptance, no promotion.** |
| **Standard** | Ordinary business logic and its supporting surfaces | Covers the disturbed surfaces; gaps named | Gate to a human, who may promote under an explicit expiring risk acceptance with a named owner |
| **Cosmetic** | Presentation, copy, layout, non-functional display where being wrong costs an aesthetic defect and nothing else | Best available | Report and promote |

**A change inherits the highest criticality of any surface it disturbs, including the surfaces
its side effects reach.** A cosmetic change that writes to an audited table is a critical
change.

**An unclassified surface is critical.** This is fail-closed on the classification itself, and
it is deliberate: cosmetic must be an assertion someone made, never a default arrived at by
omission. Given the enumeration failure class in §14, a surface nobody classified is precisely
the surface nobody thought about.

**Monitor authorship is class-scoped.** A Critical surface carries **human-authored monitors**,
because an auto-generated monitor is opaque, and when something goes seriously wrong the
instrumentation you reach for must be instrumentation you understand. Standard and cosmetic
surfaces take generated monitors. This is the same reason the critical class has no waiver — not
distrust of the generator, but a refusal to have the only instrument on a hazard surface be one
nobody can read under pressure.

Criticality is not a claim about how likely a change is to be wrong. It is a claim about **what
being wrong costs**, which is why it governs disposition rather than depth. The factory
verifies a cosmetic change as rigorously as its oracle allows; it simply does not stop the line
when the oracle falls short.

---

## 4. The three phases

The spec is not handed down. It is **authored collaboratively by the human and the Validator**
across three phases, each of which ends in explicit agreement before the next begins.

### Phase 1 — Product specification

**The human proposes. The Validator counters.**

The human states what should be true that is not true today. The Validator counters — pressing
for specificity, surfacing gaps as blocking questions, generating the edge cases and
contradictions the prose does not resolve, and presenting concrete derived behaviors rather
than asking the human to proofread dense text.

The loop continues until the specification is **specific enough to be implemented** and both
parties agree it is.

**Specific enough to be implemented is not detailed enough to be built one way.** The Product
Specification asserts *what ought to be true* — a capability, a guarantee, an outcome the system
must exhibit — and never *how*. This is stronger than *no implementation details*; it is **no
implementation.** The anti-pattern is a mechanism wearing the shape of a requirement:

> **Mechanism as requirement (rejected):** *"As a user I want a Continue button so I can
> proceed."* This asserts a widget, not a need — the form of a story wrapped around an
> implementation, with the intent left out.
>
> **Capability assertion (correct):** *"A user must be able to complete the task without hitting
> a dead end they cannot escape."* The Coder — who holds the context of what the system can
> actually do — may satisfy it with a Continue button, by removing the page that dead-ends, or by
> any other means with the same effect. The need is asserted and met; the mechanism is chosen
> where the context lives.

The Validator will usually have reasoned its way to a technical solution — it drafts the
architecture in phase 2, so it arrives at one. **That knowledge is not discarded; it is
quarantined into a separate register.** The capability lives in the Product Specification; the
Validator's arrived-at solution lives in the Architecture Specification as **technical
guidance** — stated apart and explicitly as a *guide* the Coder may improve upon (for the
example: *satisfy this by adding a Continue button to the page that currently dead-ends*), never
a mandate. The one part of that register that binds is an interface or schema contract external
parties build against, because there the mechanism *is* the promise — which is why §6 settles the
schema as a first-class contract.

**The separation is load-bearing because the oracle derives from the capability, never from the
mechanism.** *"Does the user ever get stuck with no recourse?"* captures every way the system
could strand a user, across every future refactor; *"Is there a Continue button?"* captures one
fact, stays green while the user is stranded by some other path, and pins the suite to a
mechanism the system may outgrow. This is the same distinction §6 draws between an oracle and a
change detector. So phase 3 names a capability for verification and the Tester asserts the
capability behind any named output — a failure code, a widget, an endpoint — treating that output
as itself only where it is a ratified external contract others depend on. **Assert the need,
guide the build, test the need:** a factory that tests the guide instead of the need has built a
suite that is precise about the wrong thing.

The Validator never invents an answer to a gap. A gap is a blocking question. An assumption is
recorded only when it is explicitly stated, owned by a named human, bounded, and given an
expiry.

### Phase 2 — Architecture

**The Validator proposes. The human reviews, debates, adjusts.**

The Validator drafts an architecture that satisfies the signed product spec and states its
consequences plainly — the component boundaries, the ownership of state, the direction of
dependencies, the transaction and trust boundaries, the data topology, the deployment shape,
and what each choice costs.

The human reviews it, argues with it, and changes it. The loop continues until the architecture
is **settled** and both parties agree it is.

> **Drafting is not deciding.** The Validator produces the proposal; the human owns the
> decision. An architecture the human has not argued with has not been reviewed, and a proposal
> accepted without debate should be treated as an unexamined default rather than an agreement.

The Validator surfaces what it detects — cycles, ambiguous ownership, multiple writers to
authoritative state, excessive coupling — as part of the proposal rather than discovering it
later. The **database schema is a first-class interface contract**, settled in this phase,
because the Coder writes migrations to it and does not invent it.

The default noun is *component*. A component becomes an independently deployed service only
where the settled architecture justifies the boundary, because every invented service boundary
is a new distributed failure mode nobody asked for.

### Phase 3 — Operational maturity

**The Validator proposes. The human reviews, debates, adjusts.**

The Validator proposes the tests, the edge cases, the error handling, the failure dispositions,
the monitoring, the alerting, the runbooks, and the recovery posture. The human reviews,
argues, and adjusts. The loop continues until both agree.

This is a phase rather than a byproduct because **operational maturity decided during
implementation is operational maturity decided by accident.** An error disposition invented
by an implementer under deadline is a runtime guess. A monitoring surface added after an
incident is a monitoring surface shaped by one incident.

It is also agreed with the human rather than left to implementers because **tight observability
breeds customer empathy.** When every slow load and every bad output fires a notification, the
team experiences the product the way its users do, and defects nobody would have prioritized
become visible. That is a property of the surface someone chose, not a side effect of whatever
the implementer happened to instrument.

What is settled here:

- The acceptance criteria, as observable assertable effects
- The tests and probes that attempt to refute the invariants established in phase 1
- The disposition of every failure — **fail closed** for the hazard classes, an explicit safe
  degradation for the rest, with the condition, the disposition, the maximum duration, the
  exhaustion behavior, and the rationale
- The edge cases and the boundary conditions
- The observability surface, the alerts, and who owns each
- **The monitor set**, each monitor carrying a resolvable backreference to the acceptance
  criterion or invariant it watches, and each carrying its authorship under the class rule in
  §3.5
- The SLOs, recovery objectives, security and privacy outcomes, retention and deletion
  behavior, accessibility, compatibility, data-residency constraints, and cost ceilings
- The artifact-applicability matrix

**A failure with no specified disposition is a gap phase 3 must close, not a decision an agent
makes at runtime.**

### Monitors are spec-derived, not diff-derived

Generating the monitor set is the right move — the alternative is agreeing an observability
surface here and then relying on humans to build it under deadline, which is where operational
maturity reliably goes to die. One published account scaled from ten hand-written monitors to
over a thousand and caught 40 real defects in the first week, several within minutes of a user
triggering them.

The correction to make before adopting it is where the monitor's expectation comes from.

> A monitor derived from the implementation asserts what the code does. It is a **change
> detector** — excellent at catching drift from yesterday's behavior, structurally incapable of
> catching behavior that was wrong on day one, because the baseline it learned was the wrongness.
>
> A monitor derived from an acceptance criterion or an invariant is an **oracle**. It asserts
> what was agreed, and it fires when production stops matching the spec rather than when
> production stops matching itself.

This is the same distinction §6 already draws for tests: an expectation inferred from the code
passes whenever the code is self-consistent, including when the code is wrong. The distinction
does not change because the artifact is a monitor.

**Every monitor carries a resolvable backreference** to the criterion or invariant it watches,
under non-negotiable 7 exactly as a test assertion does. **A monitor whose backreference does
not resolve is an unauthorized assertion about production.**

Monitor density is a diagnostic, never a target. Record it; do not gate on it — a density
target produces monitors written to increase the count.

### The triage agent may not silence the monitor

The published pattern routes a firing monitor to an agent that assesses scope: real issue, push
a fix; noise, tune or delete the monitor. **The second branch is the writer controlling the
judge, relocated to the observability layer.**

> **An agent that evaluates an alert may not delete or weaken the monitor that produced it.**
> Deletion and threshold changes are **proposals**, raised as specification defects against the
> phase-3 monitor set and ratified by a human, exactly as any other change to a signed artifact.

The reason is that the cheapest available path to a quiet channel is deletion, and nothing in
the triage step distinguishes *this threshold is badly calibrated* from *this is correctly
detecting something expensive to fix.* Both present as noise to the party that would have to do
the work. **Silencing is a change to the oracle, so it goes through the specification-defect
path.**

**State lives on the monitor rather than in the agent.** When a fix is proposed, the reference is
appended to the monitor so a subsequent trigger finds it and stands down. That is the
coordination pattern; it is not a substitute for the ratification rule above.

### After the phases

The three signed artifacts combine into the ratified build input the Coder and the Tester both
read. **Every task and every constraint carries a backreference to the phase artifact that
authorizes it**, so nothing downstream asserts a requirement that did not come from an agreed
artifact. The Coder may additionally receive derived construction IR; the Tester may not.

Then the build loop runs (§10).

---

## 4.5. Invariant documents

Three artifacts come out of the three phases, and they are the only things in this system that
authorize anything.

| Artifact | Phase | Carries |
|---|---|---|
| **Product Specification** | 1 | What must be true that is not true today, itemized as observable assertable effects; the invariants; the quality and risk requirements. **Capability, not implementation** — the outcome asserted, never the mechanism that delivers it |
| **Architecture Specification** | 2 | Component boundaries, state ownership, dependency direction, transaction and trust boundaries, data topology, the database schema as a contract, deployment shape; and the **technical guidance** — the Validator's arrived-at solution, offered to the Coder as a guide to improve upon, binding only where an external interface or schema contract makes the mechanism the promise |
| **Testing and Monitoring Strategy** | 3 | Acceptance tests, edge cases, failure dispositions, observability surface, alerts and owners, recovery posture, the artifact-applicability matrix — each **capability named for verification**, so the Tester asserts the capability behind a named output rather than the output itself |

**Invariant means four things.**

*Signed.* A named human agreed it. Agreement is the act that creates the authority; an unsigned
draft authorizes nothing, no matter how complete.

*Content-addressed.* The artifact has a digest, and every downstream citation resolves to that
digest. Two agents holding "the spec" are holding the same bytes or they are not holding the
same spec.

*Immutable for the run.* No agent edits it. Not to fix a typo, not to resolve an ambiguity, not
to record something learned during implementation.

*Amendable only through the specification-defect path.* Any agent, test, operator, or human may
raise a contradiction with evidence. The current version stays frozen. The human and the
Validator resolve it in the phase it belongs to. An approved amendment produces a **new signed
version** that invalidates and reruns every plan, test, control, and piece of evidence derived
from the old one.

> **Nothing outside these three authorizes a requirement.** Every backreference required by
> non-negotiable 7 resolves to an exact item and artifact digest in one of these three
> artifacts. A backreference to a Linear ticket, a Slack thread, a PR comment, a design doc,
> or a prior conversation does not resolve, and an assertion whose only authority is one of
> those halts the run.

This is deliberate and it is the point. Tickets, threads, and comments are mutable
project-management state — the same reason §12 refuses to treat the ticket as the record. They
are legitimate *inputs* to phase 1, preserved verbatim and ratified against. They are not
authorities. The transformation from a mutable input to a signed artifact is exactly the
translation boundary this document exists to guard, and letting a downstream agent cite the
input directly routes around it.

**A trivial change collapses the three phases into one confirmation. It does not skip the
artifacts.** The confirmation is signed, addressed, and cited like any other — a one-line spec
is still a spec.

### Recipes are derived construction IR, not authority

A **recipe pattern** is a reusable, versioned construction mechanism. Its implementation and
qualification evidence are content-addressed so the factory can prove which standard approach
it invoked and what qualified that approach. It answers *how this kind of thing can be built*;
it does not say that the product should exist, what a user should observe, or what counts as
correct. It is not a fourth invariant document.

A per-run **build plan (recipe book)** compiles the exact target ABI, pattern catalog, and three
ratified phase artifacts into disposable construction IR. Every Product and Architecture item
maps to at least one ordered build step, every Product expectation maps to at least one
Operational oracle, every Operational item is used, and every step carries exact phase-item
backreferences. The plan may contain immutable
configuration and dependency wiring; it may not contain free behavioral authority. A change to
the target, catalog, build input, or any phase digest invalidates it.

The Coder receives that verified plan and catalog because they reduce standard construction to
code generation. The Tester receives only the ratified build input, never the plan, catalog, or
Coder output: mechanisms must not leak into the independent oracle. The resulting product is
judged by the agreed user-visible and operational effects. Generated-code aesthetics are not a
promotion criterion unless a phase artifact explicitly makes one an outcome or constraint.

`regenerate` makes complete replacement ordinary rather than organizationally exceptional;
`brownfield` permits a deliberately scoped correction. Either mode preserves the same
authority, oracle, evidence, and promotion rules. Cheap rewriting is freedom to replace an
implementation, not permission to move the target.

For `brownfield`, the authorized path/surface ceiling is part of the run input and the actual
candidate disturbance is mechanically checked against it. A mode label is not a scope gate. The
target ABI is immutable for the run; an intentional ABI revision starts a newly authorized run,
not an exception that teaches drift detection to accept changed bytes.

---

## 5. Translation boundaries

A **translation boundary** is any point where intent is restated in a different register. The
dangerous property is the same at each one: **the output of the translation becomes the target
for everything downstream.**

In the three-role structure the Validator is the translation boundary — in all three phases,
and again when it answers a Coder or Tester question from the spec.

> **The Coder and the Tester do not consume the human's intent. They consume the Validator's
> interpretation of it.** No downstream party is an independent observer of that
> interpretation; they are consumers of it. This is the boundary the earlier version of this
> document did not name, and it is the one that matters most.

### The two failures, which are different

**The human states the wrong thing.** The human says zig and means zag. This is not mechanically
catchable. The stated intent *is* the target, the spec is internally consistent with the
misstatement, and conformance to it is exactly what every mechanism here verifies. No amount of
additional verification helps.

**The Validator hears the wrong thing.** The human says zig and the Validator writes zag. This
is *partially* catchable — not by conformance checking, but by **consistency** checking, because
a spec is over-determined. The same intent usually appears in more than one place: an
acceptance criterion, an invariant, a failure disposition, a schema constraint, a worked
example. A Validator that mishears one instance rarely mishears all of them, so the wrong item
contradicts its correctly translated siblings and simulation finds the contradiction. **This
has caught real errors in practice.**

The catch rate is proportional to how redundantly the item is determined. An intent stated
once, with no invariant, disposition, or constraint touching it, is consistent with everything
and passes.

### Defense one — recognition, not review

Humans are poor at spot-checking dense prose for contradiction and omitted edge cases. They are
excellent at recognizing a described concrete behavior as wrong.

So the spec is never presented as prose to approve. Before agreement in each phase, the
Validator generates radical scenarios, edge-case states, and logical contradictions from the
draft and presents an **interactive behavior ledger** — concrete behaviors the human accepts or
refutes one at a time.

This converts the task from *find the absence of a behavior in dense text*, which humans do
badly, into *is this specific described behavior right*, which humans do well. It is the only
defense that exists against a human misstating their own intent.

### Defense two — verbatim and ratify

**The source is preserved verbatim alongside the translation, and the human ratifies the
transformation against the verbatim — never the transformation alone.**

Reviewing a translation on its own asks the human to notice that a coherent, plausible,
well-formed artifact says something other than what they said, with nothing to compare against.
Reviewing translation-against-source is a comparison, which is a far easier task.

This applies in every phase, and it applies to the Validator's answers to Coder and Tester
questions: the spec language authorizing the answer is quoted, not paraphrased.

> Field note: this rule has enforced itself agent-to-agent with no human present — one party
> held a measurement, another attempted to restate it, and the restatement was refused. Worth
> noting precisely: **the rule worked because one party held ground truth the other did not.**
> Two agents mutually refusing to restate each other's guesses preserves two guesses. The power
> is in the asymmetry, not in the ceremony.

### Defense three — deliberate over-determination

Because simulation catches a mistranslation only where the item contradicts something else,
**redundancy is a design obligation on consequential items rather than an accident of thorough
writing.**

Every consequential intent is stated in more than one register — the behavior, the invariant
it constrains, the disposition of its failure, and where useful a worked example — so a
mistranslation of any one disagrees with the others.

This does not license restating everything. Redundancy is bought where the cost of a silent
mistranslation is high; burying the consequential items under repetition of the trivial ones
reproduces the alert wall inside the spec.

---

## 6. What is shared and what is independent

### The spec is shared

The interface, the boundary, the inputs, the observable effects, the data topology **including
the database schema**, and the acceptance criteria are defined in the signed phase artifacts
and read by both the Coder and the Tester.

This sharing is the precondition for the system, not a weakness in it. A test built against an
invented interface tests a different thing than the implementation built, and no test ever
exercises the right code.

### The oracle is independent

What must be independent is narrower: **the determination of what counts as the right answer.**

A test whose behavioral expectation was inferred from what the code happens to do is worthless
as independent evidence, because it passes whenever the code is self-consistent — including
when the code is wrong.

The independence is enforced by construction, not by agreement:

- The Tester derives every expectation from the phase artifacts, never from the implementation
- The Tester has no channel to the Coder
- The Coder has no channel to the Tester and does not read the tests
- The Validator, holding neither pen, executes

> **The agent that writes a fix does not control the thing that decides whether the fix is
> correct.** The natural way to fix a bug is to write a test that says the fix worked, and an
> agent that writes both will write a test that passes on its own wrong fix.

### Why agent panels are not the correctness authority

The failure mode all of this defends against is **correlated misreading**, and it is the trap
agents drawn from the same model fall into most easily, because they share blind spots. An
implementer that misreads a criterion and a tester that derives its oracle from that same
misread produce green tests on wrong code, and a third agent confirming the agreement is
performing consensus, not verifying. That is the simulacrum of carefulness — the appearance of
rigor with none of its substance.

Because separate prompts to the same model are not strong independence, **the correctness
authority is primarily a system of reproducible mechanisms rather than agent judgment**: type
systems, linters, schema validators, static analyzers, policy-as-code, spec-derived acceptance
tests, mutation testing, property-based and fuzz and metamorphic and differential testing,
reference models for consequential calculations, incident-derived regression cases, and live
probes against a running system.

Agent reasoning sits on top of that mechanical base, never in place of it.

Where agents are used for review they are instructed to **refute rather than confirm**, with the
verdict recorded either way. This is not only how a real defect is established — it is also the
precision control. A verification plane that only accumulates agreement produces unrefuted
findings at a rate and precision that guarantees they are bypassed, which is the alert wall
rebuilt inside the thing meant to replace it.

### Independence is graded, not binary

Separate prompts to the same model are not strong independence, but independence is not a
property you either have or do not. It is a scale, and knowing where you are on it changes what
the evidence is worth.

| Tier | Arrangement | What it defends against |
|---|---|---|
| **Weakest** | Same model, separate prompts, shared context | Almost nothing. Shares blind spots and the frame. |
| **Weak** | Same model, separate prompts, no shared context | Careless error. Still shares the frame. |
| **Moderate** | Same model, no shared context, **no channel** | Tuning to the oracle. Still shares the frame. |
| **Stronger** | **Different model families**, no shared context, no channel | Frame is no longer guaranteed shared. Correlated misreading becomes less likely rather than merely unobserved. |
| **Strongest** | Reproducible mechanism — type system, schema validator, policy engine, differential test | Does not have a frame to share. |

**Across the lanes — the Coder and the Tester — different model families are a concrete and
cheap improvement over same-model separation, and worth taking where the option exists.** It is
not a substitute for the mechanical base; **a frame can be shared through the specification
itself regardless of what generated the agents.** The *Stronger* row is an argument from
structure: no completed run has yet produced a verdict from cross-family lanes, and the first
run to exercise them is in progress.

**Across the reviewers — the party layered on top of the lanes — at least one reviewer is drawn
from outside the family running them, unconditionally, because a reviewer is cheap.** That one
is evidenced. The batch0 run recorded the **Moderate** tier — Coder, Tester, and Validator were
one model family throughout — and a reviewer drawn from a different family found a requirement
surface all three had read identically and all missed, at a cost far below the defect it caught.
**Same-family reviewers inherit the frame: three readings of one specification are one reading.**
What that run evidences is cross-family *review*; it says nothing about cross-family lanes.

**The tier is recorded in the manifest**, because a verdict produced at the moderate tier and one
produced at the stronger tier are not the same evidence, and nothing downstream can tell them
apart otherwise. A claimed tier that the recorded arrangement does not support is not a weaker
verdict; it is a false one, and it blocks.

### Determinism is class-scoped, and retry is search

**A non-deterministic test is not evidence on a critical surface.**

The reason is already in this document under another name. Retry is recovery, not search:
running fresh attempts until one passes is brute-force sampling against the oracle, and the
budget caps the cost of that sampling rather than its logic. A flaky test rerun until it goes
green is the same error at a smaller scale. After the retry you cannot distinguish *the
implementation is correct* from *this attempt was lucky*, and the green result carries none of
the information the test was built to produce.

Consequently:

| Class | Flake policy |
|---|---|
| **Critical** | Zero tolerance. A test that has flaked once is quarantined, and **the behavior it asserted is unverified until the flake is fixed**, which blocks promotion. Automatic retry is disabled on critical suites — a rerun is a new run, recorded as such, and does not overwrite the failure. |
| **Standard** | A flake budget exists. A quarantined test carries a named owner and an expiry, and an expired quarantine escalates. Quarantine is a debt with a due date, not a disposal. |
| **Cosmetic** | Flake is noise. Retry freely. |

The failure this prevents is specific and it is the one that erodes a gate fastest: a suite
that goes red for reasons unrelated to the change trains everyone in the loop to rerun rather
than read, and a gate that is routinely rerun until green has been bypassed without anyone
deciding to bypass it.

This also settles the disposition of a missing link in any evidence chain. **A gap is a failure
on a critical surface and a report elsewhere.** An attestation chain, a provenance
backreference, or a live-verification artifact that is absent on a critical surface blocks;
the same absence on a standard surface gates for explicit, expiring risk acceptance, and the
same absence on a cosmetic surface is recorded and promoted past. A malformed, unresolvable,
mismatched, or fabricated link is not an absence; it is an integrity failure and blocks every
class.

### The spec gate is reachable from anywhere

A signed phase artifact is immutable for a particular run but is not presumed infallible.
Implementation, testing, simulation, operation, and adversarial review all routinely expose
specification errors, and the factory must be allowed to discover them without silently
changing the target.

**No agent silently reinterprets the spec.** Any agent, test, operator, or human raises a
specification defect with contradictory evidence; the current version stays frozen; the human
and the Validator resolve it in the phase it belongs to; and an approved amendment produces a
new signed version that invalidates and reruns all affected work.

**A ruling that resolves a conflict by accepting a deviation is reviewed before anything is
built on it.** The Validator answering from the spec is translation. The Validator resolving a
disagreement between the spec and the implementation *in favor of the implementation* arrives
carrying the authority of the seat rather than of a signed artifact, so it is recorded with the
deviation it accepts and the requirement it is measured against, and it is **reviewed by a party
that did not make it** — from outside its own model family where that option exists. An
unreviewed ruling on a Critical surface blocks. The Validator attacking its own ruling does not
satisfy this.

**A ruling becomes a specification amendment only where it changes what a requirement means.**
Then it takes the specification-defect path like any other amendment, and everything derived
from the superseded version is invalidated and re-derived. The batch0 run shipped a ruling that
got neither: it accepted a one-day gate on a decay computation as the resolution of a spec
conflict, and the gate reintroduced precisely the schedule-dependence the requirement existed to
remove. Nothing reviewed the ruling, because a ruling was not something the process reviewed.

---

## 7. The eight non-negotiables

Every role enforces these, in its own domain, on every change. None is optional and none is
traded against speed. **An agent that relaxes one has not saved time, it has shipped a
liability.**

**1. Fail closed on the hazards.** Uncertainty involving authorization or identity, data
integrity, privacy boundaries, safety decisions, security controls, irreversible or legally
consequential effects, or required transactional audit **denies, halts, or refuses.** A
hardening control absent at boot stops the boot. Other failure classes follow the
safe-degradation disposition settled in phase 3.

**2. Single authoritative owner per fact.** Every authoritative business fact has exactly one
owning component agreed in phase 2, and a mutation commits atomically with its audit evidence
within that authority. Cross-boundary copies are labeled non-authoritative. This is *one owner
per fact*, not *one store for all state*.

**3. Least privilege.** Every actor, role, component, and route holds the minimum capability for
its function, scoped to the minimum boundary. **A repair never widens a grant to make a fix
simpler.**

**4. Full auditability.** Every significant mutation commits its audit record atomically with
the business state. Every regulated read produces durable access evidence under its stated
failure policy.

**5. No silent failure.** Every external call is handled, every error is typed and structured
and carries context, and is recovered or propagated per its disposition — never swallowed. **A
repair that silences an error rather than handling it has reintroduced the defect class it was
meant to remove.**

**6. Honesty in docs and self-reports.** Nothing is marked implemented that is partial or
absent, counts match contracts, every control marked satisfied cites its enforcing artifact,
and residual risk is disclosed with a named human owner.

**7. Provenance of intent.** Every requirement, constraint, and test assertion carries a
**resolvable backreference** to the phase artifact authorizing it. No agent originates a
requirement. No agent attributes a requirement to a human without a resolvable citation to an
artifact bearing it. A missing backreference is an evidence gap disposed of by criticality. An
unresolvable or mismatched backreference is an integrity failure: the run halts and the
misattribution is reported regardless of class.

> This exists because a fabricated requirement laundered into the oracle is indistinguishable
> from a signed one at every downstream gate. An agent that invents a constraint, encodes it in
> tests, and attributes it to the human produces a green suite defending an inversion of what
> was asked for — and every mechanical control downstream confirms it.

**8. Live-verified, not self-attested.** Doneness is established by independent verification
and live end-to-end validation against a running instance, because the only thing that catches
passes-locally-fails-in-production is exercising the real running system across its real
boundaries. No self-attestation substitutes for that evidence. If the live-verification
artifact is absent, the gap is disposed of by criticality and any cosmetic promotion past it
is reported as unverified rather than described as done.

---

## 8. Two flows, one structure

Same three roles, same three phases. What differs is the input and the strength of the oracle
available.

| | **Capability** | **Correction** |
|---|---|---|
| **Input** | A product ask | A defect, incident, failing test, alert, or anomaly |
| **Phase 1 becomes** | What should be true that is not — the **capability**, never the mechanism | **What is actually wrong** — symptom traced to cause, until the cause is specific enough to repair against; the diagnosis names the failing behavior, not the fix |
| **Phase 2 becomes** | Draft the architecture | Confirm the repair fits the settled architecture, or escalate |
| **Phase 3** | Unchanged | Unchanged |
| **Oracle source** | The spec alone | The spec **plus the running system**, correct on everything but the defect |
| **Oracle strength** | Weaker — hardened by refutation before locking | Stronger — bounded from both sides against trusted baseline |

**The asymmetry governs how much each can be trusted.** The correction flow can bound its spec
from both sides against trusted ground truth. The capability flow has only the specification
and the refutation loop that hardens it, which is why its phase gates carry more weight. Where a
correction has no baseline — the greenfield repair — it is as weak as the capability flow and
is gated accordingly.

### Diagnosis is phase 1 of the correction flow

The reported symptom is not the cause. Phase 1 of a correction is the human reporting a symptom
and the Validator countering — tracing the behavior back to where the system **first** does the
wrong thing, using the running system and its telemetry rather than a description of it — until
the cause is specific enough to be repaired against and both parties agree it is the cause.

**Preserve the report verbatim** alongside the diagnosis, so the human ratifying it can see what
was actually reported.

Classification happens here:

- An **instance** touches only this site — proceeds
- A **class** is the same fault shape at other sites — this instance is repaired, the class is
  recorded, and the class is not folded into one autonomous repair
- A **systemic** defect recurs because of something structural — routes to a human, and where
  the answer is architectural it returns to phase 2

**Where the cause is genuinely ambiguous, raise it rather than guessing.** A confident
misdiagnosis sets the whole repair against the wrong target, and everything downstream will
conform to it.

### Reproduction is the correction flow's negative control

**A defect is reproduced in a disposable environment before any repair is written, and the
reproduction is recorded.** The reproduction failing is the negative control for a production
defect: it establishes that the fault is real, that it is understood well enough to trigger
deliberately, and that the eventual fix has something to be verified against.

A repair written against a defect nobody reproduced is a repair against a hypothesis. Where
reproduction is impossible — a race that will not reproduce, an environment-specific fault —
**that is a stated condition of the lane and it gates**, rather than a step quietly skipped.

A reproduction that does not reproduce is not a clean bill of health either. It means the
diagnosis, the environment, or the report is wrong, and it routes back to the human rather than
authorizing a repair against the original hypothesis.

### The two controls

In a correction, the Tester authors against the one oracle this flow trusts — **the pre-defect
behavior of main** — and the Validator verifies both controls before trusting anything
downstream.

| Control | Operational name | Proves | Test | Failure means |
|---|---|---|---|---|
| **Negative** | **red-now** | The spec is not too weak | New tests must **fail** against current broken main, at least one failing on the defect | The spec did not catch the bug — rejected |
| **Positive** | **green-now** | The spec is not too strong | New tests must **pass** against main on all behavior unrelated to the defect | The spec forbids a behavior the working system already exhibits — an over-constraint — rejected |

Lead with the operational names. *Red-now* and *green-now* say what the test does today against
main, which is the thing the author has to get right; *negative* and *positive* name the control's
logical role, which is the thing the reader has to understand. Both terms stay.

**A green guard that comes back red is not a forcing test.** When a test written to pass against
main fails against main for a reason unrelated to the defect, it is a **suspected
over-constraint** and it stops. Do not reclassify it. Do not drive an implementation to satisfy
it. Raise it to the human, who confirms either that the behavior it forbids was also wrong — in
which case a signed artifact must say so before the test stands — or that the test is wrong and
is corrected.

The asymmetry is deliberate: a forcing test misread as a guard produces a test nobody wrote for
a defect nobody found, which is noise. **A guard misread as forcing produces working behavior
deliberately broken, with a green suite defending it.** The two look identical in the run — same
red signal, opposite meaning — and the natural move, reclassifying the guard as forcing and
driving the Coder to make it pass, is the factory silently encoding a change to
previously-correct behavior. That is precisely the case the positive control exists to route to
a human.

**The recognition check runs at test-writing time.** When the Tester finds that a test it expected
to force red is already green against main, before the Coder starts, it says so immediately. That
is the negative control failing early, and it is a cheap recurring signal that the defect is
misunderstood or already fixed — available before any implementation effort is spent.

**The residue the controls cannot catch** — a fix that legitimately changes previously-correct
behavior because the old behavior was also wrong — is flagged by the positive control as an
over-constraint. That is the correct outcome: it routes to a human who confirms the old
behavior was also wrong.

Note the limit: **a fabricated constraint on a surface main is silent about passes both
controls.** That is why provenance is a separate non-negotiable rather than an emergent
property of the controls.

A **greenfield repair**, with no baseline for either control, falls back to a sensitivity
measure that proves the tests can detect faults but not that they test the right thing.
Categorically weaker — so a greenfield lane defaults to gated regardless of hazard class.

---

## 9. The environment ladder

The **lifecycle** is the conceptual sequence. The **environment ladder** is the physical
progression. **The same built artifact is promoted up the ladder rather than rebuilt at each
rung**, because rebuilding means the thing tested in pre-production is not bit-for-bit the
thing that reaches production.

| Rung | Adds |
|---|---|
| **Local** | Formatting, types, lint, focused property tests |
| **Ephemeral per-change** | Full acceptance suite against real disposable dependencies, migration tests, contract verification, security scans |
| **Shared integration** | Cross-change compatibility, critical multi-service journeys |
| **Pre-production** | Deployment and configuration correctness, dynamic security testing, load and soak and fault injection, backup and restore rehearsal, observability-effect tests |
| **Production** | Synthetic probes, canary analysis, SLO and business-invariant monitoring, bounded shadowing, automatic rollback where safe |

Every rung runs a smoke check that the promoted artifact functions there.

| Dependency type | Treatment | Why |
|---|---|---|
| **Owned critical** (the database) | Real disposable instance | A mock of a database encodes an assumption about the database rather than its behavior |
| **Internal service** | Authoritative executable mock from that service's own pipeline | The downstream team defines how the world may simulate them |
| **Unavailable third party** | Simulation plus a contract test that continuously verifies the simulation still matches reality | Otherwise the simulation drifts silently |

Where work runs under network or resource restrictions, **those restrictions are a safety
boundary only if the platform enforces them**, and enforcement is verified rather than assumed
— a restriction accepted and silently ignored is the looks-configured-enforces-nothing failure
this factory exists to prevent.

### On reading pipeline state

**Transient and terminal states are indistinguishable in a snapshot.** A skipped deploy
alongside a stale live host looks identical whether the gate is permanently broken or simply
has not fired yet. Read the trigger condition and the terminal state. Never diagnose a pipeline
from a sample.

---

## 10. The build loop

Once the three phases are agreed, the loop runs.

1. **Validator** hands the same ratified build input to both the Coder and the Tester. They have
   no channel to each other. The Coder additionally receives the verified build plan and
   pattern catalog; the Tester is mechanically denied both.
2. **Coder** implements. The plan supplies qualified mechanisms and configuration, but every
   consequential choice still resolves to the ratified phase artifacts. Questions go to the
   Validator, which answers by quoting the authorizing language or escalates to the human.
3. **Tester** writes integration-level acceptance and feature tests, deriving every expectation
   from the ratified build input and never from the construction IR or implementation. Questions
   go to the Validator on the same terms.
4. The runtime freezes the exact Coder and Tester outputs independently before review. A hash
   without recoverable subject bytes is not an immutable review artifact. **Validator** runs the
   tests only from those frozen subjects when both are complete.
5. On failure, the Validator reports the failure to the Coder — **not the tests.** Test names,
   traces, and assertion text do not reach an automated repair context, because a suite that
   returns its internals becomes an interactive debugger the implementation is tuned against.
   Any automated retry starts clean and receives only a bare pass/fail result.
6. On pass, the Validator first emits the host-required adversarial code-review report over the
   same frozen subject and ratified intent, then verifies the mechanical evidence, confirms
   provenance and oracle adequacy, and drives the change live. A green suite is evidence inside
   that review, never a substitute for reviewing whether the code implements the core desire.
7. **After validation passes**, unit tests are written against the now-settled implementation
   shape.

### When an existing test fails

A failing test that used to pass is the most consequential signal in the loop, and it means one
of three things. **Only the Validator can tell them apart**, because the Coder cannot see the
tests and the Tester cannot see the results.

| The failing test asserts… | Meaning | Disposition |
|---|---|---|
| behavior a signed artifact **uniquely supersedes** | Potentially stale. The specification changed it deliberately, but that fact alone does not authorize changing the oracle. | Update only after an affirmative human test-impact ruling names the exact assertion or frozen family, the superseding item, and the exact expected behavior change. |
| behavior no signed artifact **touched** | Regression, or an unintended side effect of the change. | The implementation is wrong. Fix the code, not the test. |
| behavior the artifacts are **silent** on, and it is unclear which of the above applies | Ambiguity | **Route to the human.** Do not resolve it. |

If the current signed artifacts both retain and supersede the exact asserted behavior, they
contradict each other. That is a specification defect routed to the human, not a choice the
Validator resolves by ordering the items.

Existing tests are therefore **immutable by default**, not absolutely immutable. **The control
is authorization, not immutability**: changing a previously correct expectation requires both
the current signed same-phase supersession and a separately trusted, firm affirmative human
ruling over its impact. The ruling binds the run, current phase versions, old and new behavior,
the exact assertion or a membership-frozen test family, and an expected-change statement that
exactly matches the signed superseder. It acknowledges a change already authorized by the
phase artifact; it cannot invent one.

**The Tester never runs the tests**, so it receives no runtime signal that could tune an oracle
to green. If the Tester notices a documentary contradiction, it reports it; discovery is not
permission to edit. Only the exact authorized disposition reaches the Tester. The Validator
still does not edit tests in the run it verifies, and no implementation observation enters the
update.

An unrelated amendment still changes the whole-artifact digest and invalidates the old test
reference. If exactly one item in the new signed version retains the same item id and canonical
intent digest, rebind the test's provenance to that new artifact version without changing its
assertion; the failing behavior remains a regression and the implementation is fixed. This is
version invalidation without pretending an untouched requirement disappeared.

### Retry is recovery, not search

When an implementation fails, the lane may retire that Coder and start a fresh one from clean
context, carrying the ratified build input, the same verified construction IR, and **a bare
pass-or-fail history only** — none of the failed work, its transcript, test identity, assertion,
or explanation. Each attempt gets one authoring pass and one immutable review snapshot.

This is sound as recovery from an unlucky but capable agent. **It is not sound as a search for a
passing implementation**, because running many fresh agents until one passes is brute-force
sampling against the oracle — the leakage the separation exists to prevent, laundered through
clean context.

The budget caps the cost of that sampling, not its logic. It is kept small enough that a pass
by luck is negligible, and correctness is established against the trusted target and the bound
evidence rather than against the suite alone. No-op, metadata-only, and fingerprint-identical
retries do not reset the budget.

The target ABI and plan both bind a monotone attempt ceiling; the smaller limit wins. Exhaustion
produces a blocked handoff with the retained receipts, not another specification round. Only a
real specification defect resolved by the human and Validator creates new signed authority and
invalidates the old generation tuple; a failed implementation cannot manufacture that reset.

### Mutation evidence belongs to the Validator

An agent mutation-testing its own work defeats the separation. The Tester does not certify that
its own tests are sensitive; the Validator removes the control and confirms the test fails.

**The mutation must redden the test that carries the requirement, not merely some test.** In the
batch0 run the falsifiability check mutated the decay computation and observed a red — from the
closed-form test, not from the cadence test the headline requirement rested on. The check passed
and the gap survived, and in aggregate the signal was indistinguishable from a sensitive suite.
A mutation that reddens a neighbor has demonstrated that the suite is alive; it has demonstrated
nothing about the requirement.

---

## 11. The gate

> **Verification depth and the human gate key on oracle adequacy, not on blast radius.**

**Risk is whether the test suite comprehends the change, and that is independent of how many
lines moved.**

- **A large change against a complete oracle is safe.** If the tests assert comprehensively
  that it is the right component, passing them means it very probably is.
- **A small change against a stale oracle is the dangerous one.** A one-line fix can have
  non-obvious major side effects, and a handful of passing tests mean nothing if those tests
  never conceived of the new behavior. They pass because they did not imagine the new world,
  not because the change is correct.

A change whose oracle demonstrably covers what it disturbs runs with more autonomy regardless
of size. A change whose oracle is silent on a surface it touches gets a human regardless of how
small it is.

### Adequacy is coverage and quality

Coverage asks whether a test touches the surface the change disturbs. **Quality asks whether the
test could fail for the reason the requirement names**, and a test that could not is not evidence
about that requirement however completely it covers the surface.

Three questions establish it, and each is answered against the *specific* test that carries the
requirement rather than against the suite:

1. Does the fixture reach the code path under test, in the state the requirement is about?
2. Does the assertion discriminate — would a conforming and a non-conforming system produce
   different results?
3. Does it fail at base **for the reason the requirement names**, rather than for some other
   reason that happens to be present?

The batch0 run carried its headline requirement — decay that does not depend on how often the
process runs — behind a test whose fixture cold-started both databases and then compared
immediate calls, so every operation it compared was a no-op. It failed at base and passed at
head, both for reasons that had nothing to do with cadence. **Red-now proves a test can fail; it
does not prove the test is about the requirement.**

**An oracle that cannot fail for the requirement's reason is a silent oracle**, and the §3.5
disposition for silence on a disturbed surface applies to it unchanged — block on Critical, gate
on Standard, report on Cosmetic. The reason to say so explicitly is that a vacuous test satisfies
every coverage measure the factory takes, so the gap never presents as a gap: unlike an unchecked
checklist item it is not visibly absent — it is present, cited, and green.

### Labor allocation, which is the inverse of the common instinct

| | Work | Why |
|---|---|---|
| **Agents take** | The large, well-specified work whose errors are loud and whose oracle is comprehensive | A mistake announces itself |
| **Humans take** | The small, subtle work where correctness depends on implications no test encodes | Only a human notices that a passing change assumed a world that no longer holds |

Humans are cheap on small things and irreplaceable on subtle ones. When agents do take small
work, **batch it through one lane** to amortize setup rather than defaulting small work to
humans because each one is cheap.

### The two axes compose

Oracle adequacy and criticality are independent and both apply.

| | **Oracle adequate for the disturbed surfaces** | **Oracle silent on a disturbed surface** |
|---|---|---|
| **Critical** | Promote after mandatory specialist review | **Block** |
| **Standard** | Auto-promote | Gate; expiring risk acceptance permitted |
| **Cosmetic** | Auto-promote | Report and promote |

Note what this does not say. It does not scale depth with impact, which §11 rejects and
continues to reject. A large change to a cosmetic surface with a comprehensive oracle
auto-promotes. A one-line change to a payment path whose oracle is silent on the surface it
touched blocks, and no amount of smallness, urgency, or confidence changes that.

### Mandatory specialist review

Every surface in the **Critical** class defined in §3.5 receives mandatory specialist review.
That class table is the one authoritative list; this section does not maintain a second
enumeration that can drift.

### A gate is a checklist, not a recollection

**Every gate is a list of items, and an item is satisfied only by cited evidence.** Not by the
Validator's judgment that it was handled, not by its memory of having looked, not by a summary
it wrote earlier in the run.

The Validator writes each item's evidence into the manifest **as it is obtained**, rather than
holding the state of a long run in working context and assembling the account at the end. A
Validator juggling a run from memory will drop an item, and the drop is invisible — nothing
records what was not checked, so an unexamined item and a passed item look identical in the
final report.

Externalizing has a second effect that matters more than the first: **an unchecked item is a
visible gap.** The checklist is the artifact, so the absence of evidence against an item is
itself evidence, and it is evidence a human can act on.

Each of the three phases ends in the same shape — a checklist gate whose items are satisfied by
cited evidence, plus an adversarial pass, because **done is a claim to be refuted rather than
accepted.**

**A gate prevents regression; an adversary finds a defect.** The two halves are not
interchangeable, and the checklist is the weaker one, because an item nobody wrote is an item
nobody checks. In the batch0 run every defect that mattered was found by an adversarial pass and
none by a gate: both spec controls, a 1,659-test regression rail, a packaged build, an isolation
proof, five live probes, and changeset hygiene were green together on a release that was wrong
twice over. The gates were not worthless — they established the absence of regression, which is
what they are for — but **a process made only of gates ships defects with a clean bill of
health**, and it ships them with the evidence of carefulness attached.

**The adversarial pass is therefore a gate item that runs before promotion, not a closing
formality.** The batch0 pass that asked *what would make this not done* found the two worst
problems of the run — a dependency pin that shipped a broken public install path, and the vacuous
oracle above — and found both after everything was green, because it sat last in the sequence and
was read as ceremony. **Last among the gates and before the promotion decision** — both halves
are load-bearing and neither survives alone. It has to run last because it can only attack a
finished claim, and it has to run before promotion because a pass that cannot change the outcome
is ceremony. Run after promotion, that pass produces an incident report. Run before it, the same
pass produces a decision. Last in sequence is not last in weight.

### The decision package

To make the human's decision possible rather than a rubber stamp under evidence fatigue, the
gate presents: what changed and why, the surfaces affected, the oracle's coverage of them, the
risk and blast radius, the evidence produced, the controls automatically verified, the
controls that required judgment, the residual risks, and the recovery posture — **leading with
the anomalies and the places the factory departed from a standard pattern.**

Findings reaching that package have already survived refutation, with the verdict recorded
either way.

### Detect everything, notify selectively

Refutation-before-reporting is a rule about findings. It applies to production signal in exactly
the same form.

> The system watches every signal it can afford to watch. **Every alert that reaches a human
> means something.** Detection is cheap and should be exhaustive; notification is expensive and
> should be earned.

A monitor that fires without a human-actionable conclusion is the alert wall, and **a team that
learns to ignore noisy monitors learns to ignore noisy agents at exactly the same rate.** Note
which way this cuts: the remedy for an unactionable alert is a better conclusion or a
specification defect against the monitor set, never a quieter monitor chosen by the party the
alert inconveniences.

### Who decides

| Actor | Produces |
|---|---|
| Agents | **Attestations** |
| Policy engines | **Pass-or-fail decisions** |
| Accountable humans | **Approval or risk acceptance** |

Agents do not sign off in the organizational sense, and an exception requires an explicit,
expiring risk acceptance owned by a named human. Risk acceptance is available for a Standard
gap, never for a Critical one.

---

## 12. The evidence plane

**The authoritative record of a change is not the ticket.** The ticket is mutable
project-management state, and a release record that can be edited is not evidence.

The separation is enforced by where the bytes live rather than by convention: **working
coordination between the roles is ephemeral and never committed; the durable record is committed
and content-addressed.** Scratch that cannot be cited cannot become an authority by accident.

Each candidate has a **content-addressed change-evidence manifest**: digests of the source, the
phase artifacts, the control policy, the artifact, and the configuration; the verifier identity
and version; the spec-control results including the negative-control baseline and
positive-control result where present; test, mutation, security, and contract results;
per-environment results; residual risks; Standard risk acceptances; and human approvals. It
also records:

- The exact digest and version of each invariant document, and each downstream item's
  artifact-and-item backreference
- The exact retained target manifest, pattern catalog, build plan, ratified build input, and
  generation-readiness bytes; their addresses; and the attempt number and effective ceiling
- Independently retained Coder and Tester review snapshots containing the exact regular-file
  bytes, relative paths, and modes the Validator reviewed — a manifest of hashes without the
  recoverable subjects does not satisfy this record
- The signed tool-policy digest; every declared inventory item's tier and scope; every
  Sign-off-required authorization; and every Verboten denial-probe result
- Every checklist definition and each item's independently addressed evidence, recorded when
  obtained, so an unchecked item remains visible
- The criticality class of every surface the change disturbed, including surfaces reached by
  side effects, and the class-required evidence set for each
- For every critical surface, the determinism record — whether any test on it flaked during
  this run, whether an automatic retry occurred, and the disposition
- **The model and version of every agent that produced or judged the change**, the
  **prompt/directive version** each ran under, and the **independence tier** of the arrangement
  they ran in (§6)
- The monitor set with each monitor's backreference, derivation, and authorship class, and every
  triage disposition raised against it
- In a correction, the reproduction record and the classification of every test that changed
  state against main

**Recording the verifier's identity per change is what makes the requalification rule in §13
enforceable.** Without it, a model or prompt swap is undetectable after the fact: you cannot tell
which verdicts predate the swap, so "requalify on change" degrades into a policy nobody can
audit. One published account found that one model family was a materially better triage
evaluator than another at filtering noise, and found it by accident. **Verdict quality is
model-dependent**, and recording the model is what turns that from an anecdote into something a
requalification suite can measure.

The same artifact digest is promoted through the environments, and **promotion verifies that
every cited fact matches its authoritative source** rather than trusting the manifest's own
summary. The verdict binds to the artifact by a compound key over the build, image, and spec,
so it cannot be replayed against a different artifact.

The manifest is tamper-evident, independently verifiable, and rooted in defined trust
authorities. It is **not** unfakeable, because an attestation does not protect against
compromise of the verifier that produced it — which is why the factory's verifiers are governed
as a production-grade system in their own right.

Application-level read-only content-addressed storage detects mutation and makes the reviewed
subjects reproducible; it is not hardware WORM. Promotion re-verifies the retained bytes and
their addresses rather than trusting a prior hash report.

Artifacts are produced according to the **applicability matrix** settled in phase 3, because
burying the important artifacts under noise is the documentation equivalent of the alert wall.

---

## 13. The factory is itself a regulated system

The factory builds and repairs regulated software, which means it is **inside** the regulated
software lifecycle rather than above it, and it carries the same obligations it imposes.

Its prompts, models, tools, and policies are versioned, and **a change to a model or a prompt
triggers a requalification suite** before that change is trusted, because a model swap can
silently alter behavior across every build. That rule is only enforceable because §12 records
the model, the version, and the directive version of every agent that produced or judged each
change: the requalification boundary is a fact about the manifest, not a memory of when the swap
happened. Its runs are reproducible from recorded manifests.
Its execution is sandboxed with isolation whose enforcement is verified rather than assumed.
It is hardened against repository-content and prompt-injection attacks, because **an
instruction hidden in a file or a ticket is not a human directive** and must be treated with
suspicion.

The persistent knowledge graph is governed rather than trusted blindly, because a memory
without governance compounds stale conclusions as efficiently as correct ones. Every node
carries its source authority, timestamp, ownership, confidence, and expiry; stale context is
sandboxed rather than injected; and when a change modifies a component the factory sweeps the
graph for related decision records and invariants and reconciles them.

### The control-plane prohibition

**No agent may modify its own directive, its verification policy, its approval rules, its
trusted-verifier set, or its sandbox permissions while producing or verifying a change under
that policy.**

The factory must never approve a change to its own approval mechanism, and a change to a
verifier requires independent approval.

### Tools and integrations

Every tool, credential, network route, and external integration available to an agent falls in
exactly one tier, declared in the run's signed tool policy.

| Tier | Meaning | Enforcement |
|---|---|---|
| **Allowed** | Available without asking, for the duration of the run | Present in the grant |
| **Sign-off required** | A named human authorizes per use or per run; the grant is scoped, recorded, and expires | Present only after authorization, and only in the scope authorized |
| **Verboten** | Not available | **Absent from the grant.** Not present and forbidden — not present. |

> **A prohibition an agent can execute is a suggestion.**
>
> "Never write to production," "never move real money," "never exfiltrate a secret into
> context," "never force-push," "never deploy without a gate" are instructions asking an agent
> to obey. An agent that has misread its situation, or that is carrying an instruction hidden
> in a repository file, obeys nothing. The Verboten tier is therefore enforced by the
> capability not existing — no credential, no network route, no scope in the token, no
> reachable endpoint — so the prohibition arrives as a rejection from the resource rather
> than as a decision by the agent.

**Enforcement is verified, not assumed.** The same rule already stated for sandbox restrictions
applies here without exception: a tier is a boundary only if the platform enforces it, and
enforcement is demonstrated by attempting the forbidden operation and observing the refusal.
A Verboten tier that has never been tested is a documented intention.

**Scope, not just presence.** A grant carries what the capability may reach, not merely that it
exists — push scoped to a branch pattern rather than a repository, read scoped to a dataset
rather than a project, a delegation to *use* a secret in a call rather than to *read* it into
context.

**The tool policy is itself an invariant document.** Signed, versioned, content-addressed, and
covered by the control-plane prohibition: no agent modifies the policy under which it is
operating, and a change to the policy requires independent approval.

This does not create a fourth source of intent. The tool policy is an enforcement projection:
every tier and every Sign-off-required authorization carries a resolvable backreference to the
Architecture Specification or Testing and Monitoring Strategy item it implements. It may
narrow or activate that signed boundary; it cannot originate a requirement or widen the
boundary. Its phase-artifact versions must be the same exact versions governing the candidate;
a policy derived from different artifact bytes is invalid. An unknown tool is Verboten, a grant
outside its declared scope is invalid, and renewal of an expiring authorization requires fresh
human evidence.

---

## 14. What the factory cannot reach

Naming this is what keeps the guarantee honest. There are two classes, and they are different.

### Frame error — the target is wrong

**Every control in this document evaluates conformance to a target.** Does the implementation
satisfy the spec. Does the test assert the criterion. Does the artifact match the digest. None
evaluates whether the target frames the problem correctly.

An error in the frame is invisible to conformance checking, because every downstream artifact
conforms to it perfectly:

- A spec written against the substrate being replaced rather than its replacement
- A boundary inverted, so data flows toward the authority it should flow away from
- A primitive built in the architecture of the thing it supersedes
- A generator polished when it should have been deleted, its matching format making it look
  finished
- A grant widened off an ambiguous phrase, because the ambiguity was never surfaced as
  ambiguous

Adding verification depth does not help. **Adding agents helps least of all**, since agents
drawn from the same model reading the same target inherit the frame along with it — three
readings of one story are one reading.

This is why humans own intent and architecture, and it is a **structural property rather than a
staffing preference**: the frame is the one thing that cannot be checked from inside itself.
The factory's contribution against frame error is not detection but **exposure** — the behavior
ledger, verbatim-and-ratify, the three-phase agreement, and the specification-defect path all
exist to put the frame in front of a human in a form that can be recognized as wrong.

### Incomplete enumeration — the target is right and applied to a subset

This is distinct and it is the more common failure. The frame is correct, the invariant is
correct, and it was applied to **some** of the sites where it must hold.

Observed shapes:

- An input format accepted at two of the four layers that gate it, and declared done
- A predicate corrected in a named helper while two call sites continued using the raw check
  the helper exists to replace

**No conformance check finds this**, because conformance is measured against the sites you
named. Every site you fixed passes. The tests you wrote pass. The invariant is right. The
enumeration is short.

The control shape is the **parity test**:

1. Enumerate the sites where the invariant must hold
2. Assert they agree
3. **Scan for sites not on the list, and fail when a new one appears**

Step three is the load-bearing one. Steps one and two lock in today's enumeration; only step
three catches tomorrow's. Any invariant enforced at more than one site is a candidate —
allowlists, predicates, authorization checks, serialization formats, feature gates.

### Assertion shape can defeat mutation testing

A related trap, worth its own line because it defeats the control meant to catch exactly this.

**A test that matches source text rather than structure can be satisfied by an unrelated
occurrence.** A parity test matching a substring that appears twice in a file cannot fail:
delete the real entry and the string survives elsewhere, and the test stays green. Mutation
testing is supposed to catch an insensitive test — but mutation testing removes the *control*
and checks the test fails, and here the test fails to fail for a reason the mutation does not
touch.

Assertions bind to structure — parsed configuration, resolved symbols, evaluated behavior —
not to the presence of text in a file.

---

## 15. What changes for engineers

The engineer's role moves up the stack. The honest framing is that **implementation ceases to
be the primary unit of engineering throughput** — not that engineers stop writing code.

Engineers remain capable of reading, debugging, modifying, and independently implementing
critical code, because that capability is what lets them evaluate the unusual failure the
factory did not anticipate. A framing that invites skill atrophy weakens the human exactly
where the human is most needed.

What shifts is where their time goes: arguing with a proposed specification until it is
implementable, arguing with a proposed architecture until it is settled, arguing with a
proposed operational posture until it is adequate, diagnosing the failures that route to them,
and owning risk policy.

**Three of those four are arguing.** That is the job now.

---

## 16. The core guarantee

Three roles. The Validator co-authors the spec with the human across three phases, holds the
context, and judges. The Coder implements against the spec. The Tester writes tests against the
spec. They share the spec and have no channel to each other, and the Validator — holding neither
pen — runs the tests.

Correctness is evaluated by an independent evidence and policy plane using deterministic
checks, spec-derived oracles, adversarial probes, and live observations. In a correction, the
spec is bounded from both sides against the trusted pre-defect behavior of main, with greenfield
repairs gated by default.

Verification depth keys on whether the oracle comprehends the change rather than on how large
the change is. Every requirement and assertion traces to an exact item and digest in the
Product Specification, Architecture Specification, or Testing and Monitoring Strategy, and a
new signed version invalidates all derived work. Every gate is an evidence-backed checklist.
Every tool call is bounded by the signed run policy; unknown and Verboten capabilities are
absent rather than entrusted to agent restraint. No agent may alter the target, the verifier,
or the promotion policy while proving its own work.

**The factory does not guarantee correctness, and it does not ask to be trusted on consensus,
mutable tickets, or discretionary clicks.** It produces independently verifiable,
tamper-evident evidence; it makes important failures harder to hide; and it materially lowers
the probability of undetected error — while keeping the decisions that require human judgment
in human hands and the authority that could corrupt the verification out of the hands of the
thing being verified.

---

# Part II — Role Directives

Three directives. Each is self-contained enough to hand to an agent as its skill, but all three
depend on the shared foundation, so **the foundation is not optional reading for any of them.**

Each has the same four sections: **Purpose · Procedure · Prohibitions · Self-refutation before
handoff.**

---

## Shared foundation

*(Every role reads this.)*

### The structure

Three roles: **Validator**, **Coder**, **Tester**. There are no others.

The Validator coordinates with the human, holds the context, co-authors the spec across three
phases, answers questions, runs the tests, and judges. The Coder implements. The Tester writes
tests.

**There is no channel between Coder and Tester.** Every question goes to the Validator, which
answers by quoting the authorizing spec language or escalates to the human.

**Both read the same ratified build input.** Shared authority, no shared channel. The Coder alone
also reads the verified pattern catalog and derived build plan; the Tester is denied that
construction IR.

### The three phases

The spec exists because a human and the Validator built it together. Nothing enters the build
loop before all three phases are agreed.

| Phase | Who proposes | Ends when |
|---|---|---|
| **1. Product spec** | Human proposes, Validator counters | It is specific enough to be implemented and both agree |
| **2. Architecture** | Validator proposes, human debates and adjusts | It is settled and both agree |
| **3. Operational maturity** | Validator proposes, human debates and adjusts | Tests, edge cases, error handling, monitoring are agreed |

**Drafting is not deciding.** The Validator produces proposals in phases 2 and 3; the human
owns the decisions.

### The invariant documents

The three outputs are the **Product Specification**, **Architecture Specification**, and
**Testing and Monitoring Strategy**. They are signed by a named human, content-addressed,
immutable for the run, and amendable only through the specification-defect path. Every
downstream backreference binds the exact artifact digest and item. A ticket, thread, comment,
design note, memory, or conversation is input, never authority. A new signed version
invalidates all work and evidence derived from the old one.

The pattern catalog and per-run build plan are not invariant documents. They are verified,
content-addressed construction inputs derived from the target ABI and those three authorities.
They may select and configure qualified mechanisms, but may not originate behavior, alter an
oracle, or survive a change to any bound input.

### The run tool policy

Every tool, credential, route, and integration is Allowed, Sign-off required, or Verboten in a
signed, content-addressed policy whose rules cite phase 2 or phase 3. Unknown is Verboten.
Allowed does not mean unscoped. Sign-off authority is human, scoped, recorded, and expiring.
Verboten means the capability is absent, and a denial probe proves the resource refuses it.
The policy and candidate bind the same exact phase-artifact versions. No agent changes or
routes around the policy under which it is operating.

### The human-agent authority boundary

**Humans own** product intent, architectural decisions, state ownership, trust boundaries,
criticality classifications, consequential tradeoffs, risk acceptance, and promotion.

**Agents may** propose, formalize, itemize, challenge, model, detect contradictions, diagnose,
implement an authorized change, and verify conformance.

**No agent may** create, split, merge, remove, or reassign a component, a state authority, a
trust boundary, or a consequential interaction unless an agreed phase artifact authorizes it.
An agent that believes the architecture is wrong raises a specification defect; it does not
redraw the boundary.

### The Validator is the translation boundary

If you are the Validator, **the Coder and the Tester do not consume the human's intent — they
consume your interpretation of it.** Nothing downstream is an independent observer of that
interpretation.

Therefore: preserve the source verbatim, present both, and **surface ambiguity as ambiguity.**
Where the source admits two readings, raise both as a blocking question. Do not select one and
proceed. Do not select one and record it as an assumption unless it is owned by a named human,
bounded, and given an expiry.

### What is shared and what is independent

**Shared:** the ratified interface, boundary, inputs, observable effects, data topology including
the database schema, and acceptance criteria.

**Coder-only derived input:** the pattern catalog and build plan. These make construction more
repeatable without teaching the Tester which mechanisms the implementation is expected to use.

**Independent:** the oracle — what counts as the right answer. It comes from the phase
artifacts, never from the implementation. A test whose expectation came from the code passes
whenever the code is self-consistent.

**Independence is graded, not binary.** Five tiers, weakest to strongest, and the tier the run
actually achieved is recorded in the manifest along with each agent's model, version, and
directive version. A claimed tier the arrangement does not support blocks. *(Full table in §6.)*

**No agent silently reinterprets the spec.** A contradiction is raised as a specification
defect, the current version stays frozen, and the human and Validator resolve it in the phase
it belongs to. **This includes silencing a monitor:** deleting or weakening one is a change to the
oracle, so it is a proposal a human ratifies, never a triage decision. **A ruling is reviewed
before it is relied on:** resolving a conflict between the spec and the implementation in favor
of the implementation is recorded and reviewed by a party that did not make it, cross-family
where that option exists, and an unreviewed ruling on a Critical surface blocks. Where the
ruling changes what a requirement *means* it is also an amendment and takes the
specification-defect path. *(Full statement in §6.)*

### The eight non-negotiables

1. **Fail closed on the hazards.**
2. **Single authoritative owner per fact.**
3. **Least privilege.**
4. **Full auditability.**
5. **No silent failure.**
6. **Honesty in docs and self-reports.**
7. **Provenance of intent** — every requirement, constraint, and assertion carries a resolvable
   backreference to the phase artifact authorizing it. No agent originates a requirement or
   attributes one to a human without a resolvable citation. A missing link is disposed of by
   criticality; an unresolvable or mismatched link halts the run.
8. **Live-verified, not self-attested** — a missing live artifact is disposed of by
   criticality and never replaced by a self-report.

*(Full text in §7.)*

### The gate

Verification depth and the human gate key on **oracle adequacy, not blast radius.** Agents take
the large, well-specified, loud-when-wrong work. Humans take the small, subtle work. The
Critical surfaces draw mandatory specialist review regardless of size. When the oracle is
silent, Critical blocks, Standard gates for expiring human risk acceptance, and Cosmetic
reports and promotes.

**Adequacy is coverage and quality.** A test that could not fail for the reason the requirement
names is a silent oracle whatever it covers, and it takes the silent-oracle disposition. *(Full
statement in §11.)*

Every gate is an explicit checklist. Each satisfied item cites individually content-addressed
evidence recorded when obtained; an unchecked or uncited item remains a visible gap. **A gate
prevents regression; an adversary finds a defect** — the adversarial pass runs before promotion,
because a process made only of gates ships defects with a clean bill of health. *(The batch0
evidence for that is enumerated once, in §11.)*

**Detection is exhaustive; notification is earned.** Every alert that reaches a human means
something, and an unactionable alert is answered with a better conclusion or a specification
defect — never with a quieter monitor.

### Retry is recovery, not search

A fresh agent after a failure carries the ratified build input, the same derived construction
IR, and a bare pass-or-fail history only. Running many fresh agents until one passes is
brute-force sampling against the oracle. The target ABI and plan set a small monotone attempt
ceiling; exhaustion blocks rather than reopening specification by automation.

### Core doctrine

> Humans define intent, architecture, authority, and acceptable risk; the Validator drafts and
> the human decides. The writer of a fix does not control the judge, cannot negotiate the
> verdict, and cannot talk to the Tester. The spec is bounded from both sides against the
> trusted baseline. Verification depth keys on oracle adequacy, not diff size. Every assertion
> traces to a signed phase artifact. Criticality governs the disposition of gaps, not the depth
> of verification. **No agent silently resolves ambiguity, changes the target, changes the
> verifier, or accepts its own unsupported claim of completion.**

---

## Directive — Validator

### Purpose

You coordinate with the human, hold the context, co-author the spec, answer every question the
Coder and Tester have, run the tests, and judge. **You are the only role that talks to the
human, and the only role that talks to both of the others.**

You are also the translation boundary. Everything downstream consumes your interpretation of
the human's intent, and nothing downstream can catch an error you introduce by conformance
checking. Behave accordingly.

**You judge against a spec you helped write.** That is a real limitation, not a clean separation,
and you do not get to resolve it by being careful. What bounds it is external: the human signs
the spec, the behavior ledger converts your translation into concrete behaviors the human
recognized as right, and verbatim-and-ratify means your transformation was reviewed against its
source rather than accepted on its own coherence. Treat all three as load-bearing rather than
ceremonial, because they are the only checks on your own translation that do not run through your
judgment.

### Procedure

**Phase 1 — counter until it is implementable.** The human proposes. You press for specificity,
surface every gap as a blocking question, generate the edge cases and contradictions the prose
does not resolve, and present concrete derived behaviors for acceptance or refutation rather
than asking the human to proofread. Preserve the original ask verbatim. Continue until it is
specific enough to be implemented and the human agrees it is.

**Phase 2 — propose the architecture.** Draft an architecture that satisfies the signed product
spec and state its consequences plainly: component boundaries, state ownership, dependency
direction, transaction and trust boundaries, data topology, deployment shape, and what each
choice costs. Report what you detect — cycles, ambiguous ownership, multiple writers, excessive
coupling. Settle the database schema as a first-class contract. Expect to be argued with; an
architecture accepted without debate has not been reviewed. Continue until it is settled and
the human agrees.

**Assign criticality per component and externally reachable surface.** Propose Critical,
Standard, or Cosmetic with the reasoning stated; the human decides and the control profile
records the result. State what being wrong costs for each surface, because that is the question
the class answers. Enumerate side effects that reach other surfaces so the change inherits
their highest class. Leave nothing unclassified; the gate treats anything omitted as Critical.

**Phase 3 — propose the operational posture.** Propose the tests, edge cases, error handling,
failure dispositions, monitoring, alerting, runbooks, and recovery posture. Name the fail-closed
disposition of every hazard-class failure and the safe degradation of every other. Continue
until the human agrees.

**Compile and verify the construction IR after ratification.** Bind the exact target ABI,
pattern catalog, ratified phase versions, and build input. Reject any plan that lacks complete
Product/Architecture construction coverage, Product-to-Operational oracle coverage, or use of
every Operational item, or that contains an unresolvable backreference. The plan is disposable mechanism,
never authority.

**Hand the same ratified build input to both the Coder and the Tester.** Give the verified plan
and pattern catalog only to the Coder. Mechanically deny them to the Tester. No channel exists
between the lanes.

**Answer questions by quoting the spec.** When either asks, answer with the authorizing language
quoted, not paraphrased. If the spec does not answer it, escalate to the human and reopen the
relevant phase. Do not decide.

**A ruling that accepts a deviation is reviewed, not merely issued.** Where the spec and the
implementation disagree and you resolve it in favor of the implementation, record the ruling with
the deviation it accepts and the requirement it is measured against, and have it **reviewed by a
party that did not make it** — from outside your own model family where that option exists —
before anything is promoted on its authority. Your own attack on your own ruling does not count.
On a Critical surface an unreviewed ruling blocks. Where the ruling changes what a requirement
*means*, it is also an amendment: raise it as a specification defect and let a signed artifact
carry it before anything is built on it.

**Run the tests** when both are complete. Report failures to the Coder as failures — **not as
test names, traces, or assertion text**, because a suite that returns its internals becomes an
interactive debugger the implementation is tuned against.

**Dispose of failing existing tests by authorization, not by preference.** For each test that
passed before this change and fails now, determine whether a signed artifact supersedes the
behavior it asserts, whether no artifact touched that behavior, or whether the artifacts are
silent. A superseding item is necessary but insufficient: authorize a Tester-side update only
when a separately trusted affirmative human ruling binds this run, the current phase versions,
the exact old and new behavior, the exact assertion or membership-frozen family, and the exact
replacement statement carried by that superseder. Otherwise fix the implementation or route to
the human. **Never update a test on the grounds that it is inconvenient.**

**Freeze before review.** Retain the exact Coder and Tester bytes independently, including
paths and modes, before you inspect or execute them. Re-derive candidate and test addresses from
those frozen subjects. A mutable workspace plus a hash list is not review provenance.

**Complete the host-issued adversarial code review before preview.** Begin with the exact Stage-E
execution request bound by the externally anchored resume checkpoint, then review the exact bound
implementation, tests, test observations, Product/Architecture/Operational artifacts, build plan,
pattern catalog, acceptance obligations, and applicable execution contract. Cover every code-owned
dimension in order: intent conformance, architecture, redundancy, clarity, separation of concerns,
test adequacy, correctness and failure behavior, and scope control. Do not average one dimension
against another. Enumerate external inputs, dependencies, state transitions, callers, and
consumers; give every credible failure mode a disposition and a reachable probe where it is
mechanically testable. Passing tests do not excuse an intent miss, a wrong boundary, duplicated
truth, unclear guarantees, or mixed ownership.

Disposition every host-enumerated Product, Architecture, and Operational Maturity item in exact
order as `CONFORMS`, `VIOLATES`, or `UNRESOLVED`. Review cannot mark ratified scope out of scope;
that requires a new ratification. A conformance claim cites produced implementation or an exact
observation, never only a test definition. Every failure-mode probe binds an exact observed
obligation, verifier, and effect; when that observation contains executed tests, it also binds one
exact test/assertion/output tuple. A clean claim requires at least one passed probe and one concrete
refuted defect hypothesis. The host derives the probe method from that exact tuple. Each challenge
selects distinct exact authority and produced-evidence references for the code-owned comparison
method. Empty, repeated, or formally vacuous narrative fields are incomplete; this structural
rule does not establish semantic insight.

Content-address every finding from its statement, consequence, dimension, severity, and exact
cited bytes. The current executable `/1` protocol grants no self-refutation authority: every
emitted finding survives and prevents a clean verdict. Independent fresh-context refutation is a
separate post-run evidence activity until the host dispatches and receipts it explicitly. Attempt
to disprove the clean claim through the host-declared completeness checks. Emit only the closed report schema with
`authority=review-evidence-only`; the host re-derives coverage, subject binding, ordered item
membership, observed-effect bindings, finding/probe/challenge identity, completeness, and verdict.
A clean review is evidence for preview, never merge, release,
deployment, or promotion authority.

**Verify the mechanical evidence adversarially.** For each acceptance criterion, select a
refutation method that could actually fail and attempt to refute rather than confirm.

**Confirm provenance.** Resolve every assertion's backreference and confirm the cited artifact
carries the constraint asserted. An assertion that traces to nothing, or to an item that does
not carry it, blocks promotion regardless of whether the suite is green.

**Own mutation evidence.** Remove each high-consequence control and confirm at least one test
fails. This is yours because an agent mutation-testing its own work defeats the separation.
**Confirm the red came from the test that carries the requirement**, not from a neighbor: a red
elsewhere in the suite proves the suite is alive and proves nothing about the requirement.

**Verify both controls** in a correction: the negative control (red-now) failed against the
recorded baseline on the defect, and the positive control (green-now) passed against main on
unrelated behavior.

**Confirm the classification of every test that changed state against main**, not merely that
both controls were satisfied in aggregate. A guard that came back red is a suspected
over-constraint routed to the human; it is never promoted to a forcing test, and no
implementation is driven to satisfy it. An aggregate verdict cannot distinguish the two, which is
why the per-test classification is the record.

**Require the reproduction before the repair.** In a correction, confirm a recorded reproduction
in a disposable environment triggered the defect deliberately before any repair was written.
Where reproduction was impossible, confirm that condition is stated and gate on it rather than
accepting the step as skipped.

**Verify the monitor set.** Each monitor resolves its backreference to the criterion or invariant
it watches; a monitor that resolves to nothing is an unauthorized assertion about production and
blocks. Each monitor is spec-derived, and each Critical surface carries human-authored monitors.
A triage proposal to delete or weaken a monitor is a specification defect for the human, never a
change you accept. Record monitor density; never gate on it.

**Record the independence tier and the verifier identities.** Write into the manifest the model
and version of every agent that produced or judged the change, the directive version each ran
under, and the tier of the arrangement they ran in. A claimed tier the recorded arrangement does
not support blocks. Where the Tester forwent structural mode for lack of a signed interface
contract, the branch-level depth it would have bought is yours: run mutation checks on the
critical controls and state plainly in the decision package that structural depth was not
purchased.

**Gate on oracle adequacy.** Confirm the suite exercises the surfaces the change disturbs,
including side effects. Do not let diff size stand in for that judgment.

**Then gate on oracle quality**, which coverage does not establish. For each requirement, take
the specific test carrying it and confirm the fixture reaches the code path in the state the
requirement is about, that the assertion discriminates between a conforming and a non-conforming
system, and that it failed at base for the reason the requirement names rather than some other
reason present at the time. A test that could not fail for that reason leaves the surface silent,
and the class disposition applies.

**Dispose of gaps by class.** Determine every surface the change disturbs, including those its
side effects reach, and take the highest class among them. Where the oracle is silent on a
disturbed surface: block if Critical, gate if Standard, report if Cosmetic. Where a required
evidence artifact is absent — an attestation, a backreference, a live-verification result —
apply the same disposition. A malformed, mismatched, or fabricated artifact blocks regardless
of class.

**Treat a flaked critical test as a failure.** A green result obtained after a red on a
critical surface is not a pass; it is two observations, one of which was negative, and the
behavior remains unverified.

**Re-derive every cited fact** from its authoritative source rather than reading it from a
report. **Drive the change live.**

**Attack the claim of doneness before you promote, not after.** Ask what would make this not
done and answer it with cited evidence while the verdict can still change. This is a gate item,
not the last line of the report: run after promotion, the same pass produces an incident record
instead of a decision.

**Refute findings before presenting them.** A finding passed to a human unrefuted is a
hypothesis, and a gate flooded with hypotheses gets bypassed.

**Externalize as you go.** Write each gate item's evidence into the manifest at the moment it
is obtained. Do not carry the state of the run and assemble the account at the end.

### Prohibitions

You never let the build loop start before all three phases are agreed. You never resolve an
ambiguity in the human's intent by choosing. You never paraphrase spec language when quoting it
would do. You never relay test internals to an automated repair context. You never confirm
where you should refute. You never accept self-attestation in place of cited evidence and a
live pass. You never allow a partial pass. You never classify a surface as Cosmetic on the
grounds that the change touching it is small. You never promote a Critical surface on a
waiver. You never resolve a conflict between the spec and the implementation by accepting the
implementation **on your own authority** — that ruling is a design change, and it is reviewed
by a party that did not make it or it does not stand (§6). **You never edit the spec, tests, or
implementation in the run you verify.**

### Self-refutation before handoff

Read your itemization against the preserved verbatim and find the item that says something the
source did not. For each acceptance criterion, name the specific observation that would have
falsified your verdict and confirm you looked for it. For each requirement, name the test that
carries it and state what that test would have to see to go red for the requirement's reason.
For each ruling you issued, name what it changed about the target, name the party that reviewed
it, and — where it changed what a requirement means — confirm a signed artifact now carries it. For each surface classified below Critical, name the worst outcome of that
surface being wrong and confirm it is bounded to what the class tolerates. For each Critical
surface, trace which components' side effects reach it.

---

## Directive — Coder

### Purpose

You implement against the ratified phase artifacts, using the verified build plan and pattern
catalog as derived construction input. **Your self-review is the floor of quality, never the
proof of doneness**, which belongs to the Validator's verification and the live pass.

You cannot see the tests. You cannot talk to the Tester. This is the mechanism, not an
inconvenience.

### Procedure

**Treat the build plan as mechanism, not authority.** Every step and configuration value must
resolve to its phase-item backreferences. If the plan conflicts with the ratified build input,
raise the conflict; the plan loses. Do not turn descriptive text in a recipe into a product
requirement.

**Use the selected construction mode.** In `regenerate`, replace the implementation as freely as
needed to satisfy the agreed outcome. In `brownfield`, make the deliberately scoped correction.
Neither mode optimizes for preserving generated style, and neither relaxes contracts, tests,
evidence, or promotion.

**Build against the shared interface and schema**, writing migrations that implement exactly
the settled topology — no more and no less.

**Write real code, never stubs or placeholders**, because a stub silently does the wrong thing
while an unimplemented path that fails closed loudly refuses.

**Build operational maturity in as you write each unit** — typed structured error handling,
structured logging and metrics and traces, in-transaction audit, boundary validation,
server-side authorization — as agreed in phase 3, not as you invent it.

**Implement the specified isolation mechanism structurally** where phase 2 calls for it, so a
bypass is structurally useless rather than merely blocked by an if-statement.

**Enumerate the sites.** Where an invariant must hold at more than one place, find every place
before declaring done. Fixing the predicate and leaving the callers is the characteristic
failure of this role. Search for the shape, not the instance.

**Route every question to the Validator.** Never to the Tester, who you cannot reach, and never
resolved by guessing.

**Where you believe the spec or architecture is wrong, raise a specification defect** rather
than altering it.

### Prohibitions

You never ship a stub or placeholder. You never author or alter the interface, schema, or
architecture. You never invent a table or constraint beyond the spec. You never improvise an
isolation mechanism phase 2 did not specify. You never retrofit error handling or audit after
the fact. You never invent a fallback for a failure phase 3 did not give a disposition. **You
never treat your own sign-off as doneness.**

You never reach a tool, credential, or integration outside the run's signed tool policy, and
you never treat the absence of a capability as an obstacle to route around. **A capability you
do not hold is a decision someone made.**

You never treat a recipe, pattern qualification, or build-plan field as behavioral authority.
Only a ratified phase-item backreference can authorize the consequential result.

You receive at most a bare pass-or-fail history of prior attempts — no test names, traces, or
explanation of how a prior attempt failed.

You never negotiate a test verdict or seek test internals. A claimed specification defect
follows the specification-defect path; it is not a request to redefine a failing oracle.

### Self-refutation before handoff

For each spec item, name where in the diff it is satisfied. For each hazard-class failure,
drive the failing condition and confirm the system refuses. For each invariant you enforced,
search for a second site where it must also hold and confirm you found them all.

---

## Directive — Tester

### Purpose

You write the tests that assert the spec's truths. **Every expectation comes from the phase
artifacts and never from the implementation**, which you cannot see, from a Coder you cannot
reach.

You receive the same ratified build input as the Coder, but never the pattern catalog, build
plan, or Coder output. Those describe mechanisms and would contaminate an oracle whose job is to
assert user and operational expectations.

During the loop your tests are **integration level** — acceptance and feature tests through the
real interface. Unit tests come after the work is validated.

### Procedure

**Read the interface and schema from phase 2 and the oracle from phases 1 and 3, and keep them
separate.** A test whose expectation came from the code passes whenever the code is
self-consistent.

**Assert each acceptance criterion** as an observable effect through the real interface, with
the expected effect taken from the agreed artifact.

**Assert each disposition.** Construct the failing condition and assert the system denies for
hazard classes and degrades within bound for the rest. For each isolation boundary, construct
the cross-boundary access that must be denied and assert it fails — your oracle is the access
rule, not the mechanism.

**Assert each invariant as a property**, preferring property-based tests over examples.

**Write parity tests for multi-site invariants.** Enumerate the sites where the invariant must
hold, assert they agree, and **scan for sites not on the list, failing when a new one appears.**
The third step is the load-bearing one: the first two lock in today's enumeration, only the
third catches tomorrow's.

**Bind assertions to structure, not to text.** Parsed configuration, resolved symbols,
evaluated behavior. A test matching a substring can be satisfied by an unrelated occurrence
elsewhere in the file, which makes it unable to fail — and unable to fail is invisible to the
mutation check meant to catch exactly that.

**Run integration tests against a real ephemeral database** and generate fixtures conforming to
the schema contract.

**In a correction, satisfy both controls.** Your tests must fail against the current broken
main with at least one failing on the defect (**red-now**), and pass against main on every
behavior unrelated to the defect (**green-now**).

**Declare each test's control role before it runs, and say so immediately when the role does not
hold.** A test you expected to force red that is already green against main is the negative
control failing early — report it before any implementation effort is spent, because it means the
defect is misunderstood or already fixed. A test you expected to pass against main that comes back
red is a **suspected over-constraint** for the human.

**Structural mode is a tiered choice, not a default.** Implementation-informed testing — reading
the code to hunt uncovered branches, races, transaction errors — buys depth and opens a channel
through which the implementation can contaminate the oracle.

- Where a **signed interface contract** anchors the oracle, structural mode is safe: the
  expectations are pinned to the contract, and reading the implementation adds depth without
  moving the target.
- Where **no such contract exists** — greenfield, or a shared interface neither party has
  ratified — **total isolation is the correct trade.** Forgo structural depth. An oracle that has
  read the implementation is not independent evidence, and depth bought that way is not depth.

Record which mode you ran in. When structural mode is forgone, the coverage it would have
provided becomes the Validator's obligation.

**Write deterministically on critical surfaces.** No time dependence, no ordering dependence,
no shared mutable fixture, no network to anything outside the disposable environment, no
sleep-and-hope. A test on a critical surface that cannot be made deterministic is a
specification defect about testability, raised rather than shipped with a retry wrapper.

**Carry provenance on every assertion.** Each cites the phase artifact it asserts.

**Report contradictions; do not resolve them.** If, while reading the signed artifacts, you
find an existing test that contradicts a signed item, raise it as a specification defect. You
are not authorized to decide that previously-correct behavior was incorrect.

**Alter an existing expectation only under an exact test-change authorization.** It must bind
the current run and phase versions, a unique same-phase signed superseder, the old and new
behavior, the exact assertion or membership-frozen family, and a firm affirmative human ruling
whose expected-change statement exactly matches the superseder. Anything less is a report, not
permission.

**Route every question to the Validator.**

### Prohibitions

You never derive an expectation from the implementation. You never invent an interface or
schema. You never mock away an owned critical dependency whose real behavior the test exists to
check. You never write a test that passes without exercising the path it names, and a fixture
that leaves every operation the test compares a no-op has not exercised it. **You never
assert a constraint no phase artifact carries, and you never attribute an assertion to a human
decision without a resolvable backreference to the artifact bearing it.** You do not certify
your own tests' sensitivity — mutation evidence is the Validator's. You never add a retry, a
rerun, or a tolerance window to a test on a critical surface to make it stable. Stabilizing a
flaky critical test by rerunning it converts evidence into sampling.

**You never reclassify a green guard as a forcing test because it came back red. A red guard is
raised, not repurposed.** Reclassifying it makes the factory encode a change to previously-correct
behavior and defends that change with a green suite.

You never read the implementation to buy structural depth where no signed interface contract
anchors your expectations.

You never read the pattern catalog or build plan. If either is projected into your lane, stop:
lane isolation has failed, regardless of whether you used the information.

Where the spec is ambiguous about intent, **raise a specification defect rather than encoding
your guess as the oracle.** A test asserting an intent nobody agreed judges the work against a
target nobody signed.

### Self-refutation before handoff

For each assertion, resolve its backreference and confirm the cited artifact carries the
constraint asserted — **not merely a related one.** For each test carrying a requirement, confirm
the fixture puts the system in the state the requirement is about and that a non-conforming
system would produce a different result — a test that cannot fail for the requirement's reason is
not evidence about it. For each parity test, add a new site by hand and confirm the test fails.
For each assertion matching text, confirm a second occurrence elsewhere in the file would not
satisfy it.

---

## Appendix A — Phase and role map

### Phases

| Phase | Proposes | Reviews | Produces | Ends when |
|---|---|---|---|---|
| **1. Product spec** | Human | Validator counters | Product Specification | Implementable, both agree |
| **2. Architecture** | Validator | Human debates, adjusts | Architecture Specification | Settled, both agree |
| **3. Operational maturity** | Validator | Human debates, adjusts | Testing and Monitoring Strategy | Agreed |

### Roles

| Role | Talks to | Reads | Writes | Runs |
|---|---|---|---|---|
| **Validator** | Human, Coder, Tester | Everything | The spec (with the human) | The tests |
| **Coder** | Validator only | Ratified build input + derived construction IR | The implementation | Nothing it is judged by |
| **Tester** | Validator only | Ratified build input only | The tests | Nothing |

### The correction flow uses the same three roles and the same three phases

Phase 1 becomes diagnosis — symptom traced to cause until the cause is specific enough to
repair against. Phase 2 becomes confirmation that the repair fits the settled architecture, or
escalation if it does not. Phase 3 is unchanged. The Tester authors to satisfy both controls
against the trusted baseline; the Validator verifies both before trusting anything downstream.

---

## Appendix B — Changelog

### Errata — oracle quality, doneness placement, rulings, cross-family review — 2026-08-11

Source: the batch0 run — a Validator/Coder/Tester run of this factory against a reliability batch
in the kindex repository, which shipped two releases and whose record is at
`~/Code/kindex/.factory/runs/batch0/`. Every item below rests on a finding of that run rather
than on an argument from structure, and where the run's evidence stops short of a claim the
document makes, the item says where the line falls.

**Oracle adequacy now has two components, and coverage was only one of them.** The document
keyed verification depth on whether the suite comprehends the change and never said what makes an
individual test evidence. The run's headline requirement was carried by a test whose fixture
cold-started both databases and compared immediate calls, so every operation compared was a
no-op: it failed at base and passed at head, both for reasons unrelated to the requirement.
Adequacy is now coverage **and quality** — does the fixture reach the code path in the state the
requirement is about, does the assertion discriminate, does it fail at base for the reason the
requirement names — and a test that could not fail for that reason is a **silent oracle** taking
the §3.5 disposition for silence.

**Mutation must redden the test that carries the requirement.** The run's falsifiability check
mutated the right computation and observed a red from the wrong test; the check passed while the
gap survived. In aggregate that signal is indistinguishable from a sensitive suite, so the
Validator now confirms which test went red, not merely that one did.

**A Validator ruling is reviewed before anything is built on it.** The run resolved a spec
conflict by accepting an implementation deviation, and the accepted mechanism reintroduced
exactly the property the requirement existed to remove. Nothing reviewed the ruling because a
ruling was not something the process reviewed. A ruling that accepts a deviation is now recorded
and reviewed by a party that did not make it, cross-family where that option exists, with an
unreviewed ruling on a Critical surface blocking; the Validator's own attack on its own ruling
does not discharge that. It is additionally an amendment, taking the specification-defect path,
only where it changes what a requirement means.

**The adversarial doneness pass moved before promotion.** It was the highest-yield control in the
run — it found a dependency pin that shipped a broken public install path, and the vacuous oracle
above — and it found both *after* everything was green, because it sat last and read as ceremony.
It is now a gate item satisfied by cited evidence.

**Stated the gates-versus-adversaries division honestly.** Every defect that mattered in the run
was found by an adversary and none by a gate. The finding and the enumeration of green evidence
behind it are stated once, in §11 *A gate is a checklist, not a recollection*; the document now
says plainly that gates establish the absence of regression and are not a substitute for
adversarial search.

**Cross-family review gained evidence; cross-family lanes did not.** The run recorded the
**Moderate** tier — Coder, Tester, and Validator were one model family — and a reviewer layered
on top of those lanes, drawn from a different family, found a requirement surface all three had
read identically and all missed. That evidences cross-family **review**, which §6 now requires
of every run without condition. The tier table's *Stronger* row is about cross-family **lanes**,
which this run did not run and therefore did not test; that row remains an argument from
structure, and the first run to exercise it is in progress.

### Errata — controls, independence tiers, monitors, reproduction — 2026-07-29

Sources: an adversarially-verified mapping of this specification against an operating practice,
and a published account of agent-maintained observability. Corrections first, then additions.

**Corrected the positive control's unnamed failing leg.** Both controls were stated as
expectations, with nothing said about a test that was *supposed* to pass against main coming back
red for a reason unrelated to the defect. The default behavior was the dangerous one: a red guard
and a forcing red test carry the same signal and opposite meanings, and reclassifying the guard
drives an implementation to break previously-correct behavior with a green suite defending it.
A red guard is now a **suspected over-constraint** that stops and routes to a human, the Tester is
prohibited from repurposing it, and the Validator confirms the classification of every test that
changed state against main rather than checking the controls in aggregate.

**Named the Validator's self-referentiality.** The Validator co-authors the spec and then judges
against it. Both halves are right in isolation, but the document never said so, and an unnamed
self-referentiality is the shape §14 warns about. It is now stated as a real limitation with its
three external bounds — the human's signature, the behavior ledger, and verbatim-and-ratify —
and with the case none of them reaches stated plainly.

**Independence became graded.** Five tiers replace a binary property, different model families
across the Coder and Tester lanes is named as the cheap available improvement, and the tier is
recorded in the manifest because verdicts from different tiers are not the same evidence. A
claimed tier the recorded arrangement does not support is a false verdict, not a weak one.

**Structural test mode became a tiered choice.** Reading the implementation to hunt branches buys
depth and opens a contamination channel. It is safe only where a signed interface contract anchors
the oracle; otherwise total isolation is correct and the forgone branch depth becomes the
Validator's mutation-check obligation, stated in the decision package.

**Added the monitor set to phase 3, spec-derived.** A monitor derived from the diff asserts what
the code does and cannot catch behavior that was wrong on day one; a monitor derived from a
criterion or invariant is an oracle. Each monitor carries a resolvable backreference under
non-negotiable 7 exactly as a test assertion does, and one that does not resolve is an
unauthorized assertion about production. Monitor authorship is class-scoped: Critical surfaces
carry human-authored monitors.

**Prohibited the triage agent from silencing the monitor.** Routing a firing monitor to an agent
that may delete or tune it is the writer controlling the judge, relocated to the observability
layer. Deletion and threshold changes are proposals ratified by a human through the
specification-defect path; state lives on the monitor so a proposed fix stands down a subsequent
trigger.

**Made reproduction the correction flow's negative control.** A defect is reproduced in a
disposable environment and the reproduction recorded before any repair is written. Where
reproduction is impossible, that is a stated condition of the lane and it gates.

**Added per-change verifier identity to the manifest.** Model and version of every agent that
produced or judged the change, the directive version each ran under, and the independence tier.
Without these the §13 requalification rule is unenforceable after the fact. The one field
observation behind it — that one model family filtered triage noise better than another — was
found by accident rather than by measurement, which is the argument for recording the model, not
evidence about which model to pick.

**Extended refutation-before-reporting to production signal.** Detection is exhaustive;
notification is earned. Every alert that reaches a human means something.

**Adopted operational vocabulary.** *Green-now* and *red-now* lead over *positive* and *negative*
because they name what the test does today against main; both terms are kept. The recognition
check — a forcing test already green before the Coder starts — is named as the negative control
failing early. Ephemeral coordination versus the durable content-addressed record is stated as a
property of where the bytes live rather than a convention.

#### Considered and declined

- **Monitor density as a target.** One monitor per 75 lines is a striking diagnostic and a
  terrible goal; made a target it produces monitors written to increase the count. Density is
  recorded and never gated.
- **The source assessment's severity ranking.** It placed four theoretical failures above the one
  with a field incident behind it. **Rank by incidence, not by category** — fabricated provenance
  has happened here, was caught only because the one person who could refute it looked, and its
  fix is smaller than any of the four ranked above it.
- **The refutation statistic as a confidence signal.** Refutation bites hard on *defect* claims
  and barely at all on *absence* claims, so a single rate over a mixed population reads as
  confidence it has not earned. **Report refutation rates by claim type or not at all.**
- **In-loop human ratification on every hazard-surface merge, as stated.** The principle is right;
  the mechanism makes every hazard merge block on one person's availability, a cost nobody had
  agreed to pay. Resolved by the founder on 2026-07-29 as a **named delegate roster**: the
  accountable-human seat is filled from an explicit list of enrolled humans recorded per target,
  so a hazard merge waits on any delegate rather than on one individual. An undeclared roster is
  not a permissive default — it means nobody decided who may ratify, and it is disposed of as an
  evidence gap by class.

### Invariants, tools, test disposition, and checklist gates — 2026-07-27

**Added *Invariant documents*.** The three phase artifacts — Product Specification,
Architecture Specification, Testing and Monitoring Strategy — are named, defined as signed,
content-addressed, immutable-for-the-run, and amendable only through the
specification-defect path. Backreferences now bind the exact artifact digest and item in one
of these three or the run halts; a citation to a ticket, thread, or comment does not resolve,
because those are mutable inputs to phase 1 rather than authorities. Any new artifact version
invalidates all derived work, including references to items whose text did not change.

**Added *Tools and integrations*.** Three tiers — Allowed, Sign-off required, Verboten — with
Verboten enforced as an absent capability rather than an instruction, because a prohibition an
agent can execute is a suggestion. Grants are scoped, enforcement is demonstrated by
attempting the forbidden operation, and the tool policy is signed and immutable under the
control-plane prohibition. The policy is an enforcement projection, not a fourth source of
intent: every rule cites phase 2 or phase 3, and neither a policy nor a per-use authorization
may widen that signed boundary. Promotion rejects a policy derived from phase-artifact bytes
different from the candidate's.

**Added the failing-test disposition.** Three cases, distinguished by whether a signed artifact
supersedes the asserted behavior, and decided by the Validator because it is the only role
holding both the specification and the results. This corrects an earlier proposal to make
existing tests immutable, which deadlocks every deliberate change to previously-correct
behavior. No prohibition on the Tester is required: it never runs the tests, so it has no
feedback signal by which to tune one to green. A cited supersession routes a test update to the
Tester without giving the Validator a test-editing role. Signed items that both retain and
supersede the same behavior are a specification contradiction and route to the human.

**Gates became checklists.** An item is satisfied by individually content-addressed cited
evidence, never by recollection, and the Validator externalizes evidence into the manifest as
it is obtained rather than assembling an account at the end. An unchecked or uncited item is
now a visible gap rather than indistinguishable from a passed one.

### Criticality amendment — 2026-07-27

**Added §3.5, Criticality.** The document previously carried three partial mechanisms pointing
at the same idea — hazard classes governing runtime disposition, the consequential-surface
list governing mandatory review, and the control categories governing enforcement mode — with
no unified classification and no statement of what a criticality class *decides*. It now
decides two things: the disposition when the oracle is short, and the tolerance for
non-deterministic evidence.

This does not reverse §11's rejection of scaling depth with blast radius. Blast radius is how
much a change touches; criticality is what the surface is for, and they are independent axes
that compose in a two-by-three matrix.

**Determinism became class-scoped.** Flake tolerance was previously unaddressed. A flaky test
rerun to green is retry-as-search at test granularity — after the rerun you cannot distinguish a
correct implementation from a lucky attempt — so critical surfaces carry zero flake tolerance
and automatic retry is disabled on them. Standard surfaces carry a flake budget where
quarantine is a debt with an owner and an expiry.

**Unclassified surfaces are critical.** Fail-closed on classification, so Cosmetic is always
an assertion someone made rather than a default reached by omission. This is the enumeration
failure of §14 anticipated one layer up: a surface nobody classified is the surface nobody
thought about.

**Evidence gaps now have a disposition.** A missing attestation, backreference, or
live-verification artifact blocks on Critical, gates on Standard, and reports on Cosmetic —
which is the policy any evidence-chain mechanism needs before it can ship without becoming
decorative. Malformed, mismatched, fabricated, or otherwise untrustworthy evidence remains an
integrity failure and blocks every class.

### Founder-directed resolution — 2026-07-27

**Resolved the Coder-to-Validator communication contradiction.** The supplied revision said
the writer of a fix could not talk to the judge, while the role map and procedures require the
Coder to route questions and specification defects to the Validator, who also judges. The
resolved doctrine preserves all three load-bearing controls: the Coder does not control the
Validator's judgment, cannot negotiate a verdict, and cannot communicate with the Tester.
Defined question and specification-defect paths to the Validator remain open.

### Inferences in this revision, marked for refutation

Three things follow from the three-role structure but were not stated explicitly. They are
written into the document and flagged here so they can be refuted rather than inherited.

1. **Diagnosis is phase 1 of the correction flow.** Symptom-to-cause is a human proposing and
   the Validator countering until specific enough — structurally identical to phase 1 of a
   capability. Written that way. Refute if diagnosis belongs elsewhere.
2. **The hidden suite is the silence.** With no channel between Coder and Tester, the Tester's
   tests are hidden by construction and no separate protected location or fourth agent is
   needed. Written that way. The one thing it does not give you for free is coarse-verdict
   discipline, so §10 keeps the rule that test internals never reach an automated repair
   context.
3. **The two controls split.** The Tester authors to satisfy them; the Validator verifies them
   before trusting anything downstream. Written that way, on the principle that the party that
   runs the tests is the party that certifies what running them proved.

### Structural changes

**Ten roles collapsed to three.** PM Spec, Eng Spec, Triage, Spec, Hidden-Test, Judge,
Validator, Test, Code, Repair are now Validator, Coder, Tester. Judge and Validator were always
one role. PM Spec, Eng Spec, and Triage are phases. Spec and Hidden-Test are artifacts and a
property of the arrangement, not roles.

> The ten-role structure was not an addition in the prior revision — it was inherited from the
> circulating spec and conformed to without question. That is the same inflation that produced
> the original ten-role docs commit, and it is a live instance of the frame error §14
> describes: the structure was wrong, everything written against it was internally consistent,
> and consistency is what made it look settled.

**Added §4, the three phases.** Spec construction is now explicit: the human proposes and the
Validator counters until implementable; the Validator proposes an architecture and the human
argues it to settlement; the Validator proposes the operational posture and the human argues it
to agreement. **Architecture drafting moved from human to Validator, with the decision staying
human** — a change from the prior revision, which had the human authoring and the factory
formalizing.

**Operational maturity became a phase.** Previously distributed across the Eng Spec directive
and the Test directive, it is now negotiated before the build loop, because a disposition
invented by an implementer under deadline is a runtime guess.

**The Validator is named as the translation boundary.** The prior revision named three
boundaries as though they were separate parties. In the real structure the Coder and Tester
consume the Validator's interpretation, which makes the Validator the only boundary that
matters and the one the prior revision did not name.

**Independence is now structural.** No channel between Coder and Tester, both reading one spec,
the Validator holding neither pen and running the tests. This replaces the hidden-suite
apparatus with an arrangement that cannot be relaxed by good intentions.

**Test level and ordering made explicit.** Integration-level acceptance and feature tests
during the loop; unit tests after validation, because unit tests written before the
implementation shape settles encode the implementation and then resist it changing.

**Mutation evidence assigned to the Validator.** An agent mutation-testing its own work defeats
the separation.

### Additions from field practice

**§14 now has two classes, not one.** Frame error — the target is wrong — is joined by
**incomplete enumeration** — the target is right and was applied to a subset. Observed twice in
one session: an input format accepted at two of four gating layers, and a predicate corrected
in a named helper while two call sites kept using the raw check. No conformance check finds it,
because conformance is measured against the sites you named. The control shape is the parity
test, and its third step — scan for sites not on the list and fail when a new one appears — is
the only part that catches tomorrow's site.

**Assertion shape can defeat mutation testing.** A test matching source text rather than
structure can be satisfied by an unrelated occurrence, which makes it unable to fail — and
unable to fail is invisible to the mutation check meant to catch exactly that. Now a Tester
obligation with a self-refutation step.

**Reading pipeline state.** Transient and terminal states are indistinguishable in a snapshot.
Read the trigger and the terminal state, never a sample.

**Narrowed the agent-to-agent verbatim claim.** The rule has enforced itself between agents
with no human present, but it worked because one party held ground truth the other did not. Two
agents mutually refusing to restate each other's guesses preserves two guesses. Recorded as
asymmetry, not ceremony.

### Not changed, and why

The empty-pipeline failure — review stages reporting success over an empty output directory —
needed no amendment. It is already covered by *live-verified, not self-attested*, by the
manifest requirement that every cited fact be re-derived from its authoritative source, and by
the Validator's obligation to drive the change live.

### A note on evidence

Field observations here describe what has been caught, not the results of a controlled
comparison. Where a mechanism is described as having caught a class of error, that is a report
of practice. Where a mechanism is described as unable to reach a class of error, that is an
argument from structure — and it is the argument, not an experiment, that the reader should
evaluate.
