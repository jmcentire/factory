# Practice — The Diff-Intent Gate

> Every diff is checked against the declared intent it operates under, before it is
> applied or approved. A diff that alters a declared invariant is a **material change**;
> material changes are never ratified in-stream by the agent that noticed them — the
> agent stops and solicits human validation. Silence is denial.

## Why this gate exists

On 2026-07-07 an AI-co-authored docs commit (`ab45e3c`) promoted pipeline stages and
human-owned artifacts into "ten role agents" and stamped the result canonical. The drift
did not arrive as a suspicious edit; it arrived as authoritative-sounding text, and every
subsequent reader — agent and human-adjacent alike — inherited it as gospel until the
founder spot-checked it (2026-07-20). The lesson: *reading* cannot be trusted to catch
what *diffing against a quoted invariant* will. The three-roles re-assertion is the
canonical worked example — "exactly three standing agent roles" changing to any other
count is a material change, full stop.

## The rule

1. **Intent is what is signed, not what is plausible.** The reference for "material" is
   the declared intent artifact — the doctrine sentence, spec item, constraint, or
   invariant the diff operates under — never the diff author's explanation, and never
   the reviewer's sense of reasonableness. The declared inventory, with each
   statement's tier (sacrosanct / invariant-by-design / epistemic / free), lives in
   [`../DOCTRINE-KERNEL.md`](../DOCTRINE-KERNEL.md) — tier lookup precedes judgment;
   inference is only for statements the kernel does not list. A diff whose governing
   intent cannot be located is itself an escalation, not a pass.
2. **The tells are deltas in commitment language.** Flag any hunk that adds, removes, or
   rewrites: a count or cardinality ("exactly three", "one owner per fact"); a MUST /
   NEVER / ONLY / ALWAYS sentence; a named role, authority, or gate; a fail-closed /
   fail-open disposition; a scope word (all / only / except / regardless); a
   prohibition; or the promotion of an example into a rule or a rule into background.
   These are mechanical, greppable signals — run them as a pre-pass, not a vibe.
3. **Trace provenance before you solicit.** A smart flag is a dossier, not an alarm.
   Locate the earliest introduction of the changed claim (`git log -S '<claim>'`), its
   authorship (human-solo commit vs agent co-authored), and the nearest human-signed
   antecedent stating the same intent (a signed spec, a founder-authored commit, a
   captured founder statement). Classify the change: **(a) founder-intended** — cite
   the origin and proceed under it; **(b) agent-introduced with no human antecedent** —
   presumptive drift or hallucination; **(c) unintended side-effect** of an
   otherwise-intended change. The solicitation carries the before/after, the named
   invariant, and this dossier.
4. **Agents escalate; humans ratify.** The agent's only verdicts are
   "**material — soliciting validation**" (quote the exact before/after, name the
   invariant, attach the provenance dossier, stop) or "**not material — proceeding**"
   (quote the invariant as held). An agent never ratifies a material change to declared
   intent — including, especially, its own directives: **genesis and mutation of
   doctrine both require a human signature.**
5. **Fail closed.** An unvalidated material change is blocked — not deferred, not
   merged with a caveat.
6. **Self-application.** This gate governs changes to itself and to the kernel.

## The drop-in prompt (any agent lane)

> Before applying or approving this diff: locate the declared intent it operates under
> (kernel entry, doctrine sentence, spec item, constraint). Quote it. If the diff
> alters a count, a MUST/NEVER/ONLY, a named role or authority, a fail-closed
> disposition, a scope word, or removes a prohibition: trace its provenance
> (`git log -S` the claim — earliest introduction, human or agent authorship, nearest
> human-signed antecedent), then say: "This looks like a material change to declared
> intent: [before] → [after]. Provenance: [intended / agent-introduced, no human
> antecedent / side-effect]. I am not authorized to ratify it. Soliciting validation."
> Then stop. If no governing intent can be found, that is also an escalation.
> Otherwise, state the invariant you checked and that it held.
