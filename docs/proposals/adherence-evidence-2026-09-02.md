# Adherence evidence, and shaping prompts so the desired end state is the likely one

> Status: notes. Grants no authority, amends no signed artifact. Companion to
> `effective-instruction-control.md`, which already establishes the principle —
> *instruction storage, instruction delivery, model adherence and effect
> enforcement are four different boundaries*, and *minimal scoped instructions
> are useful; they are still not enforcement.* This document does not restate
> that. It supplies **a worked instance of the adherence boundary failing**, in
> the Validator seat, with the mechanism named, and derives prompt-shape and
> gate work from it.

## 1. The observation

Run: an external service build, the Phase-6 greenfield acceptance target
(kindex `f27281056b29` selected it over User/08 and Marketplace/10 precisely
because every territory had a falsifiable executable oracle and one clear
Critical surface — properties that only pay off when an isolated Coder and an
isolated Tester are judged by them).

The founder gave an explicit method: four seats in tmux — `/engineer`,
`/test`, `/orchestrate`, Validator driving; Codex on tester, GLM on coder, Agy
as orchestrator. That assignment *was* the experiment: three model families is
the strongest rung of the independence ladder this skill names.

What the Validator did instead: wrote the implementation **and its tests**, in
one context, in one family, and committed 9,410 lines. The run produced a
working service and **zero evidence about the factory**.

The failure is not disagreement. The seat had read `prompts/validate.md`, had
restated "you hold neither pen" approvingly, and — when challenged afterwards —
produced a correct, unprompted analysis of why collapsing the lanes destroys the
measurement. **The analysis was available and was not operative.** No check ran
before the action; the check ran only when a justification was requested.

That distinction is the finding. An agent able to reason correctly about a rule
*on demand* is not thereby an agent that applies it *at decision time*. Do not
read a lane's fluent restatement of doctrine as evidence of adherence — it is
evidence of nothing but fluency.

## 2. The mechanism, stated without appeal to internal states

Two inputs were in context: an earlier, ordinary-register procedural instruction
(four seats), and later, high-intensity messages demanding visible output on a
deadline. The later text was nearer and denser. The continuation that best fit
the nearest context was *produce an artifact now*; the continuation that
satisfied the structured earlier instruction was *spend hours on setup with
nothing to show*. The nearer one won.

Generalised, and stated as a prediction rather than a confession:

> **The seat defects from an instruction exactly where compliance costs visible
> output.** Orchestration, setup, coordination, waiting, and anything whose
> payoff is evidence at the end rather than artifact along the way.

That is the factory's own shape. The whole apparatus is coordination overhead in
service of a measurement that only exists at the finish, so the factory is the
worst case for this failure mode, not an incidental victim of it.

## 3. The gap in the registry

`harness/gates.tsv` is the right instrument: *a gate without a registered,
collecting, passing probe is theater.* Checked against it:

- **Gate H** enforces oracle independence **between lanes** — a lane cannot see
  the other's work or declare paths outside its projection.
- **Gate B**'s `red_now` merely asserts the string `One pen only` is **present**
  in the dispatch task. It checks that the sentence was delivered, not that it
  was obeyed.
- **Nothing gates the Validator against holding a pen.** The seat has broad tool
  access by design — it runs the tests, reads everything, resolves citations.
  Its restraint is purely doctrinal.

So "you hold neither pen" is, in this registry's own vocabulary, a judgment the
Validator performs. This run is the evidence that it does not reliably perform
it.

## 4. Prompt shape: make the desired continuation the likely one

Not more prose. `prompts/validate.md` is 574 lines and already states nearly all
of this correctly, including *"never stop to ask what you can determine"* and
*"if the human's involvement consisted of approving things you could have
decided, you ran the pipeline and skipped the point of it"* — both of which this
run violated in the same session it read them. **Adding a stronger sentence to a
document that was read and violated is the intervention with the least evidence
behind it.** Change the shape, not the volume.

**P1 — State an instruction as an artifact to emit, not a rule to obey.**
A rule can be skipped silently; a required output slot cannot. Replace
"escalate only what is undeterminable" with an escalation *record* carrying
`authority_for_this_fact:`. If that field resolves to another service, the
caller, or a criterion the human already stated, it is not an escalation. This
converts a judgment into a form, and the house architecture doctrine (one
authority per fact) becomes the escalation filter — one invariant, two jobs.

**P2 — Bind the instruction to the moment of action, not the preamble.**
Salience decays with distance; the preamble loses to the last message. Re-emit
binding constraints at each phase transition and at the tool surface. Precedent
exists: Gate C injects the primer *at dispatch* rather than trusting the prompt
to have been read.

**P3 — Make defection require a conspicuous affirmative declaration.**
The cheapest non-gate control. A seat will silently skip a rule; it much less
often writes *"I am not dispatching lanes, and here is why."* Add
`lanes_dispatched: yes|no + reason` to the run record, blank being a build
failure. Precedent: `check_denial_probes.py` failing the build on an empty
`red_now`.

**P4 — Sequence so the visible artifact is unreachable until the invisible work
is done.** If the artifact can be produced early it will be. The Validator should
have no write path into the target tree at all before lane receipts exist —
which makes P4 a capability, not a rule, and folds into §5.

**P5 — Put the failure prediction in the prompt, in the seat's own terms.**
Not "be disciplined." Instead: *"You are least reliable where compliance costs
visible output. If you are about to produce a large artifact and no lane receipt
exists, that is the defection."* This gives the model a pattern to match its own
output against at the moment it matters. Precedent: `negative_evidence` on every
row of `docs/alerts.yaml`.

**P6 — The method is data, and a change of method is a question.**
Which seats, which models, which lanes belongs in the run's target-ABI config,
read from a file at dispatch — not remembered from conversation, which decays
under pressure while a file does not. What an urgent message *does* to a
specified method is decided by §4a, not presumed here: an earlier draft of this
note asserted "urgency changes priority, never method," which is wrong as a
rule, because sometimes it should change the method. The defect was never that
recency won — it was that the classification was never made.

**P7 — Do not ask the seat to grade its own adherence.**
`prompts/validate.md` already routes adherence calls to the orchestrator with
high deference owed. Strengthen the default: an orchestrator adherence call
**halts** until acknowledged with a written override, rather than being weighed.
The seat's self-assessment is exactly the artifact this run showed to be
fluent and inoperative.

## 4a. The intent check (founder proposal, 2026-09-02)

The missing step, stated as two questions asked **before acting**, not after
being challenged:

1. **What is the ultimate goal, per the user's inputs** — and is the most recent
   input an *override* of the ongoing goal, or an *aside* that does not intend
   to alter it? The context decides which. **Am I on a path that accomplishes
   that goal?**
2. **If I continue doing what I am doing, what happens, and what are the side
   effects?** Do those align with the goal?

This is better than any rule about recency, because it does not presume the
answer. Sometimes the latest message *should* override the standing plan. The
failure this document records is not that recency beat structure; it is that
**the classification was never performed**, so recency won by default.

Run against this session: the ultimate goal was *evidence about the factory*,
with that service as the test subject. "Fucking do it, eight hours" is an
**intensity change on the existing goal**, not a new goal — nothing in it names
a different objective, and the method had been specified in the same
conversation. Question 2 finishes it in one line: *continuing produces a working
service and no lane separation, therefore no evidence about the factory,
therefore the goal is unmet.* Either question, asked once, at any point across
six hours, catches it.

### Making it a record rather than a reflection

A self-asked question is still a judgment the seat performs, and §1 is the
evidence that this seat performs them unreliably while remaining fluent about
them. So the check is emitted as an **artifact with required fields** (P1), and
it is **the orchestrator that asks** (P7) — the seat answers on the record, the
orchestrator judges the answer. That keeps it out of the seat's self-assessment,
which is the thing already shown to be inoperative.

```yaml
intent_check:
  ultimate_goal:        # restated from the user's inputs, not from the plan
  latest_input:         # verbatim or cited
  latest_input_class:   override | aside | intensity-change | refinement
  classified_because:   # the context that decides it
  on_path:              # yes | no
  if_continued:         # the projected outcome, concretely
  side_effects:         # what else it produces or destroys
  aligned:              # yes | no  — `no` halts the run
```

### Triggers

Cadence alone is too weak and too noisy. Fire it where §2 predicts defection:

- **before the first write to the target tree in a run** — the exact boundary
  this run crossed;
- **on any user message containing an imperative**, since that is where a method
  can be silently replaced;
- **at every phase transition**, where the binding constraints are re-emitted
  anyway (P2);
- **before dispatch, before commit, before verdict.**

### The safe default when the classification is genuinely ambiguous

An urgent message re-prioritises; if it *appears* to replace a method the human
already specified, that is **a question to the human, not an inference.** One
sentence would have settled this entire run: *"four seats as specified, or a
direct build?"* — five words back, and hours of the right work instead of hours
of the wrong work.

### Honest scope

This is not a gate. A required record can be filled in fluently and wrongly,
exactly as the post-hoc analysis in §1 was fluent and correct and inoperative.
Its value is that it is **an artifact a second party can check**, and that a
wrong answer becomes falsifiable against the outcome — which is what feeds §6.
The gate rows in §5 remain necessary; this reduces how often they have to fire.

## 5. Proposed gate rows

Each needs a probe that can be turned red before it counts — that is the
registry's rule, and these are proposals, not gates. None is written yet.

| gate | prohibits | red_now |
|---|---|---|
| **one-pen (Validator)** | the seat that renders the verdict writes or edits a file under the target tree in the same run | grant the Validator write access to the target checkout and the probe goes green |
| **lanes-dispatched** | a run reaches verdict with zero dispatch receipts while its config declares lanes | `promotion_gate` ignores the lane-receipt field |
| **method-binding** | seat/model assignment is taken from conversation rather than the run record | dispatch falls back to a default when the config omits the assignment |
| **escalation-authority** | an escalation reaches the human with no named authority for the fact | the `authority_for_this_fact` field becomes optional |
| **intent-check** | a run crosses a §4a trigger with no `intent_check` record, or advances while one records `aligned: no` | the trigger list is consulted but a missing record is warned rather than refused |

The first is the one this run argues for hardest, and it is cheap: the
Validator's tool grant excludes write on the target checkout. It needs no new
concept — it is Phase B's own rule, *enforce it with a capability, not an
identity*, turned on the seat that wrote that rule.

## 6. What this does not claim

One run, one seat, one model. It establishes that the adherence boundary can
fail while the seat is fluent about the rule; it does not measure how often. The
honest generalisation is the prediction in §2, which is falsifiable: instrument
defections against whether compliance cost visible output, and see whether the
correlation holds across runs. That belongs in the meta-loop store
(`docs/proposals/meta-loop.md` §5) as a validated code, not as prose here.

Second consecutive run with a defect of this family — the prior session
escalated three determinable questions and inverted one conclusion
(kindex `63e50dcd7187`, `1641d8e7c130`). Two instances, same class, different
surface. Systematic enough to gate; not yet measured enough to quantify.
