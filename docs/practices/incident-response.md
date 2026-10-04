# Graduated Incident Response — a factory operate-phase doctrine

**Under the doctrine:** this is a practice under the canonical doctrine in
[`../SOFTWARE-FACTORY.md`](../SOFTWARE-FACTORY.md). It governs what happens the instant a
signal fires. Its sibling, [Reliability as Signal](reliability-as-signal.md), governs how
every failure becomes a signal in the first place; this doc governs how a signal becomes a
proportionate response.

**Status:** operate-phase doctrine of the factory process. Target-agnostic: it names no
target and depends on no target code. Concrete tools are examples of a capability class,
never requirements.

**Provenance:** generalized from the retired Production-Grade Build Playbook, Phase 7.5
(*Graduated Incident Response*). This doc is now the canonical statement.

## The stance

**An alert is a CLAIM, not a verdict.** A firing rule asserts that something user-facing
may be broken; it has not yet earned a human's sleep. Two rules bind everything below:

1. **Every page is actionable, or it trains the operator to ignore the pager.** A page that
   fires benignly, names no action, or duplicates another page is not an annoyance; it is an
   active control failure, because the next real page arrives to a desensitized operator.
2. **Page on a confirmed, severity-classified, user-facing symptom — never on a claim.**
   Between claim and page sits automated confirmation and read-only diagnosis, whose job is
   to raise or lower severity before it costs a human anything.

Escalation matches **confirmed** severity, and automation never takes an irreversible
action on regulated data.

## The load-bearing rules

- **Classify by severity first.** Adopt a five-tier severity model (SEV1–SEV5). Only SEV1–SEV2
  (sometimes SEV3) page; SEV4+ open a ticket. Every alertable condition maps to a tier, a
  routing target, an acknowledgement clock and an update cadence, in a version-controlled
  table. When unsure between two tiers, round up; SEV2+ is a major incident. Keep severity
  (business impact) separate from priority (urgency).
- **Services emit facts; escalation decides.** A signal carries what happened and never what
  to do about it: its kind, its disposition (Recovered, Degraded or Failed), its slice
  (tenant, class, channel, connection), its cause, its quantities (count, age, streak, rates,
  time pending) and a dedupe key. It carries no tier. A service that stamps ticket or page
  has taken a decision that belongs to escalation, and every later change to routing becomes
  a change to that service. So every threshold whose only job is to choose between a ticket
  and a page lives in the alerting rules, as a declared default, not in service config.
  Product behaviour stays in the service: notices to an account, holds, suppression, a
  report of what has no working endpoint. Two checks hold the line: every signal a service
  can emit has an alerting rule or is declared metric-only, and no contract text decides a
  tier.
- **An overdue required effect signals at once.** When something that must happen (a
  required message, a key delivery) passes its target time, emit a stuck signal with its
  age immediately; re-emit it on every sweep while it stays stuck; emit a distinct expired
  signal if it expires. The alerting rule, not the service, decides from how many are stuck
  and for how long whether that is a ticket or a page.
- **Reconcile the two scales.** Map the operational page/ticket scale onto the security
  incident scale (P1–P4) explicitly. A confirmed compromise of regulated data pages the
  security path at the top tier regardless of burn rate, with any required external
  notification.
- **Name what contained it.** Every "contained, no impact" record names the control that
  engaged, with evidence — or states the luck that held and opens a gap. An event stopped
  by circumstance and filed as a working control is how the next occurrence lands on
  defenses everyone believed were tested.
- **Page only on user-facing symptoms, fast-burning.** Alert on the SLI symptom (errors and
  latency burning budget), never on a cause (CPU, connection count); causes are for
  diagnosis. Use multi-window, multi-burn-rate alerting: **PAGE** at 1h/5m @ 14.4× and
  6h/30m @ 6×; **TICKET** at 3d/6h @ 1×. Both windows must trip, with the short window 1/12
  of the long, for fast detection and fast reset. Never skip the slow-drift ticket tier —
  chronic drift is the dominant failure at low traffic. Pair the alerting with an
  error-budget policy that says what happens on exhaustion.
- **Survive low traffic.** A ratio SLI with an empty denominator burns no budget while the
  service is down. Alert at the aggregate, add absolute failure counts per thin slice, and
  run scheduled synthetic journeys so an idle outage still fires.
- **Diagnose before you escalate.** Before a top-tier page: a persistence window, a
  multi-location synthetic re-probe, and hysteresis; then a **read-only** diagnostic that
  attaches evidence to the incident. A probe failure counts as failure, never as skipped.
- **AI root-cause analysis proves; it does not correlate.** Where it is used, it forms
  hypotheses and proves or disproves each, citing the exact change or config. Probe "stale
  secret," disprove it, find the stale gateway — never rotate a live credential on a guess.
  Validate it before trusting it: precision above 80%, recall around 60%, backtested on
  closed incidents, and a hallucination check (ask about a service that does not exist; it
  must say it cannot find it). Guard recency bias and correlation-not-causation.
- **Hard human-in-the-loop.** AI accelerates diagnosis; it never acts. Diagnosis is
  read-only, ingests no regulated data, and sends no regulated telemetry to an off-platform
  service. **Never auto-remediate an irreversible or regulated effect** — no automated
  rollback of a regulated write, no automated rotation of a live credential.
- **One outage is one page.** Group and deduplicate on a stable key (`[alertname, tenant]`;
  `[alertname]` for platform-wide faults), with grouping and repeat intervals set per tier.
  Key deduplication on event **identity plus occurrence** (an occurrence index, a monotonic
  cursor, a resolve→re-fire transition) — never on event content, or the second occurrence
  of every future incident is discarded as already seen.
- **Slice the one signal.** Per-dimension SLIs by tenant, provider and route, so a broken
  slice is visible while the aggregate is green. Multi-step flows emit a journey counter
  labelled with the failing stage. Labels carry no personal data and no credentials.
- **Correlate around the event.** The incident view overlays change annotations (deploys,
  config changes, provider switches) on per-slice anomaly panels, with metric-to-trace
  exemplars. The observability plane holds no business state.
- **Tooling is a capability class.** A self-hosted alerting stack inside the target's own
  project and data boundary; graduated routing as a nested severity × tenant notification
  policy; paging through a contact point that has been test-fired. Never depend on a paging
  relay you have not exercised — a deprecated relay fails as silence.
- **Heartbeat.** Emit an always-firing dead-man's switch whose **absence** pages. A broken
  exporter, dead datasource or misrouted policy otherwise produces perfect silence that looks
  like health.

## How it rides the factory's rails

- **Operational maturity phase.** The severity table, burn-rate tiers, grouping keys and
  heartbeat are part of the Phase 3 artifact ([SOFTWARE-FACTORY.md §4](../SOFTWARE-FACTORY.md)),
  derived from the spec, not from the diff.
- **Authority separation.** The diagnostic agent observes, hypothesizes and runs allowlisted
  read-only diagnostics. It is never the authority that ships a cure, and the triage agent
  may not silence the monitor it is judged by.
- **Correction flow.** A confirmed incident's captured failure window is the reproducing
  test the repair is written against, exactly as in Reliability as Signal.

## Gate

Each item is checked with cited evidence (policy file and line, a fire-drill record with a
named human and timestamp). A release is not proven until a synthetic **page and ticket**
have each been acknowledged by a named human, with the timestamp recorded.

## Prove the negative

A green checklist is a claim. The phase passes only when each of these was attempted and
survived, with fixes made to the work, not the wording:

1. The fast-burn page reaches a human — inject errors past the 14.4× tier and time the
   acknowledgement.
2. A 60-second blip does **not** page.
3. A slow, sustained burn opens a ticket, not a page.
4. A platform fault that trips every tenant produces exactly one notification.
5. The diagnostic ran and attached evidence before the page, and a probe failure counted as
   failure.
6. The root-cause analysis disproves a planted wrong hypothesis without acting on it, and
   answers "not found" for a service that does not exist.
7. An attempt to automate an irreversible or regulated action is blocked by the human gate.
8. Stopping the heartbeat pages within its interval.
9. No personal data or credentials appear in labels, traces or diagnostic context.
10. The identical event emitted twice, separated by a resolve, arrives twice.
11. Every incident closed as contained traces to a named control with evidence.

## Anti-patterns

| Anti-pattern | What it causes |
|---|---|
| Paging on a cause | The pager fires on internals that never reach users; the operator learns it lies |
| A single-window burn alert | Slow detection, or noise, and an hour of pages after recovery |
| No slow-drift ticket tier | Chronic drift is never caught until it is an outage |
| Paging on one raw sample | One slow request over a tiny denominator pages at 2 a.m. |
| No synthetic traffic | An idle outage burns no budget and fires nothing |
| No grouping | One provider fault becomes fifty pages, and the pager gets silenced |
| Dedup on content | The recurrence is swallowed as a duplicate; silence looks like calm |
| Aggregate-only SLIs | A broken tenant hides inside a healthy average |
| Correlating RCA | It blames the last deploy and sends the human to fix the wrong thing |
| Automated remediation of regulated state | An irreversible effect, executed on a false hypothesis |
| Unmapped severity scales | A breach that burns no availability budget gets an ops ticket |
| An untested paging relay | Pages silently never arrive |
| No heartbeat | Broken alerting is indistinguishable from health |
| "Alerted" never acknowledged | The channel may route nowhere |
| Containment without a named control | The control is never built, and the next occurrence lands unprotected |
