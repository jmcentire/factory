# Lessons from a greenfield proof build — 2026-09

Written during a proof run and rewritten after it. The run's judging apparatus is
gone from this file on purpose: it was scaffolding for certifying one generation, not
a way to build software, and carrying it forward turned every bug fix into a ceremony.

What survived is about getting a thing to work.

1. **Phase A never froze the interface.** The ratified spec named fields but not wire
   shapes: HTTP paths, signature headers, token-file format, `--json` key names, signed
   document schemas. Two lanes working blind cannot converge on unnamed shapes, and 45
   Validator rulings were needed after the fact to close the gap (16 of them pure
   "shape" rulings). Rule: **the interface contract (argv, output keys and types,
   endpoints, headers, document schemas, filesystem layout, budgets) is a ratified Phase
   A artifact**, produced in Pact plan-only mode, and both the Coder and the Tester's
   harness consume the same file. A Detector Reviewer pass must check the Tester's
   harness for invented conventions and for demands a blind product cannot satisfy
   (instrument-private taxonomies, unreachable witnesses).
2. **The instrument was full of defects.** NameErrors, an impossible calendar date
   (`2026-03-60`), fixture sequencing that checked out a tree without the planted event,
   a signer attribute called as a method, unsatisfiable demands. Each masked dozens of
   nodes and each cost a full judge cycle to discover. Rule: **the Tester ships a
   null-product selftest** (a stub binary that returns contract-shaped empties) so every
   fixture path executes before handover, plus pyflakes/mypy in the Tester's own gate.
3. **The Coder provider was the wrong tool and nobody measured it.** GLM-5.3 through
   Codex/Ollama: no prompt caching, 250–450 shell commands per packet, ~300M input tokens
   over 23 packets, repeated context-loss loops, quota stalls, mid-turn crashes, and a
   final packet that regressed the vector by nine nodes. Claude coders in worktrees with
   direct judge access cleared more nodes in their first hour. Rule: **qualify the Coder
   on a synthetic packet with a token/command budget before the run; enforce ceilings in
   the launcher (compaction threshold, command cap), not in the prompt; record tokens
   per green node per provider; prefer providers with prompt caching.**
4. **The sandbox denied `.git` writes and nobody had noticed for seven attempts.** Rule:
   the launcher's qualification packet must include a commit; if the sandbox forbids it,
   the **content-addressed handoff** (Coder declares `git diff HEAD --binary | sha256`,
   Validator recomputes before judging and commits on the Coder's behalf) is the
   documented protocol from day one.
5. **Merges belong to the Coder.** Parallel worktrees doubled throughput; conflicts went
   back to the Coder as a short packet rather than being resolved by the certifier.
   Keep that.
6. **A gate whose label is predictable from a displayed field measures nothing, and
    nobody checks.** The V-9 blinded operator exercise ran for five days as a
    twenty-item human-accuracy gate. Its gold was a pure function of the destination
    line shown on screen: every `company`/`codebase` item was approve, every
    `personal`/`none` item was reject. An operator who read no statement and applied
    one rule scored 20/20. It contained no misrouted item at all, so the gate whose
    purpose is catching an operator who approves a leak had no leak in it. The Tester
    documented that limitation in its own audit and did not treat it as a defect. It
    surfaced only when the founder ran the exercise, failed it, and said the questions
    never routed anything private anywhere.

    Rule: **every judged fixture with labels ships a predictability bound as an
    executable check.** No single displayed field, and no pair of displayed fields, may
    predict the label above a declared threshold; the fixture's own selftest computes
    it and fails. For any gate that exists to catch a *bad* case, the fixture must
    contain bad cases, and the selftest asserts their count. A gate's discriminating
    power is a property to be measured and re-measured, not asserted once in prose.

    Corollary: **identical `gold_reason` strings across many items are the tell.**
    Eleven of these twenty shared the string "minimized, destination-eligible, no
    protected provenance". A reason that does not name the specific item's basis is
    not a reason, and a run of them means the author labelled by category rather than
    by item.

    Corollary: **whoever observes a human fail a gate must not author that gate's
    replacement.** The Validator found this defect by watching the founder fail; the
    Tester wrote the fix. State the direction of every post-hoc fixture change and
    check it against the failing run: if the change would have rescued the run that
    exposed it, it is suspect. Here it would not have — the disambiguation alone still
    scored 85% against a 95% floor — and that is what made the change defensible.
7. **The human-owned gates are discovered last and cost the most wall time.** Two of
    the final three reds were a rights grant and a human exercise: no product defect,
    no Coder packet, just a person who had to sign something and a person who had to
    sit down for twenty decisions. They surfaced at day five because nothing forced
    them earlier. Rule: **enumerate every gate requiring a human act during Phase A,
    name the owner, and collect them before the first Coder dispatch.** A human gate
    discovered at the end is a schedule risk that no amount of Coder throughput fixes.
8. **Never put mutable status inside an immutable digest preimage.** The auxiliary
    corpus binds its identity over the exact bytes of `RIGHTS.md`, and `RIGHTS.md`
    opens with a status line ("grant recorded; current-digest re-attestation and
    selection pending"). That line can never be corrected: editing it moves the pool
    digest, which invalidates the founder attestation the correction describes. The
    document is permanently stale by construction. The machine-readable state next to
    it (`pool.json.rights_basis`, excluded from the projection) is correct and had to
    be declared authoritative over the prose.

    This is the inside/outside distinction from Helland, violated in one file. Status
    is data on the inside: mutable, authoritative, owned. A rights grant is data on the
    outside: immutable, versioned, a reference to a point in time. Braiding them into
    one artifact means one of the two is always wrong. Rule: **digest preimages contain
    only immutable claims; every mutable field lives outside the projection and is
    named as authoritative over any prose that repeats it.** The pool manifest got this
    right for `status`, `selection_record` and `rights_basis`, and wrong for the one
    document whose bytes it hashes.
9. **Blinding covers everything the operator is told, not just the fixture file.** Having
    correctly refused to author the replacement gold for a gate whose failure it had
    observed, the Validator then announced the new fixture to the operator and described
    its label balance, its misroute count, and three of its twenty items in identifying
    detail. The operator scored 20/20; only 17 of those items were uncontaminated.

    The instrument was blinded. The briefing was not. A gate is blind only if the whole
    channel to the operator is blind, and the channel includes the status update that
    tells them the gate is ready.

    Rule: **the party that announces a blinded exercise to its subject may describe only
    that it exists, how to run it, and how long it takes.** No label distribution, no
    item count by category, no worked examples, no "note that X is a trap". If the
    operator needs reassurance that a rebuilt fixture is sound, give them the validator's
    machine-checked properties by name (predictability bound, coverage requirements) and
    never an instance. Where a briefing has already leaked, score the unleaked subset
    separately and publish both numbers.

## The one that cost the most

Real defects surfaced by pointing the product at a real company, not by any test:
a sibling product already owned `.kin/config`; the certificate endpoint returned bytes
its own verifier rejected; one corrupt ledger row hid an entire corpus; a shared
observation ledger leaked facts across repository boundaries; 148,004 commits blew an
unpageable cap and left the largest repository with no history at all.

None of these were reachable from a suite that initialises into fresh worktrees.
Use the thing.
