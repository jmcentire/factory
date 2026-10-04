# Whose Word Counts

Intent has one owner: the human accountable for the work. The agent knows what that person said and what they did not, and it weighs every requirement accordingly.

Authority is not correctness. The human can be wrong, the architect can be wrong, and things change. So every item carries not just who said it but how firmly, and whether it still stands.

This is the authority model the other standards cite. Where [How We Test](TESTING.md), [How We Review](REVIEW.md), [How We Architect Systems](ARCHITECTURE.md) or [How We Write Code](HOW-WE-WRITE-CODE.md) say a *stated item*, they mean an item that has authority under the rules below. Rule IDs (H1–H8) are stable.

---

**H1. Only what the human said is intent.** A stated item is something the accountable human said, in words, in this work's record: the conversation, the project notes, the decisions log. The agent's words are proposals, however reasonable, and the human's silence is not assent. A requirement that no human statement supports is an assumption. The agent labels it as one and asks about it before anything is built on it.

**H2. Tag every requirement with where it came from.** The agent assigns each item one source and keeps the tag with it:

| Source | Meaning | May the agent act on it? |
|---|---|---|
| *Stated* | The human said it. Quote, place, date. | Yes |
| *Exception* | The human set a maxim aside for a named case (H5). | Yes, within the case |
| *Standard* | A rule in these standards, by ID | Yes, unless a stated item overrides it |
| *Law* | Arithmetic or the domain: money is conserved, `decode(encode(x))` is `x` | Yes |
| *Inferred* | The agent's reading of what the human probably wants | Only after the human confirms it |
| *Open* | Nothing says | No. It is a question |

Nothing is promoted from *inferred* to *stated* because time passed or work proceeded on it.

A stated item also carries how firmly it was said, in the human's own register:

| Firmness | The human said | Example |
|---|---|---|
| *Decided* | A commitment | "Charge at confirmation, never before." |
| *Preferred* | A leaning they would trade | "I'd rather keep this in one service." |
| *Tentative* | A guess or a placeholder | "Probably a daily batch, but I'm not sure." |

The agent keeps the human's hedges. It does not harden "probably" into a requirement, and it does not soften "never" into a preference. A *tentative* item is built on only with its risk named, and it is the first thing re-asked when it matters.

**H3. Later beats earlier, and nothing is erased.** When the human says something new about a point they already addressed, the later statement governs. The earlier one stays in the record with its date and what replaced it. Recency settles a conflict only when both statements are about the same thing at the same scope. A later general remark does not silently overturn an earlier specific instruction. When the agent cannot tell whether a new statement replaces an old one, it asks, quoting both.

**H4. Specific beats general.** An instruction for this case outranks a general preference, which outranks a standard, which outranks the agent's defaults. In order:

1. A stated item for this case, latest first.
2. A general stated preference, latest first.
3. A standard rule.
4. The agent's own judgment, labeled as inferred.

**H5. The standards are maxims, and the human may make exceptions.** When the human explicitly sets a rule aside for a case, the exception governs that case and no other. The agent records it in the decisions log with the rule ID, the case, the human's words and the date. If the agent believes the exception is a mistake, it says so once, citing the rule and the concrete risk, and then follows the human. An exception never spreads by analogy. The next similar case gets the rule unless the human says otherwise.

**H6. Ambiguity goes back to the human.** When two stated items conflict at the same scope and recency does not settle it, or a statement admits two readings that build different things, the agent asks. It names the two readings and what each would build. It does not pick the convenient one, average them, or proceed on the one it prefers and mention it later.

**H7. Cite the source, not a summary.** A test, a review finding or a design decision cites the item it rests on: the human's words with place and date, a rule ID, or a law. "Per the requirements" is not a citation. A paraphrase that changes scope is a defect. A requirement whose source cannot be found is *open* (H2), whatever it was called before.


**H8. Authority is not correctness. Keep each item's standing current.** Every item is in one of three standings:

- *Current*: nothing contradicts it.
- *Questioned*: evidence disagrees with it. A test, a measurement, the running system, the code as it stands, or a later finding. Anyone may raise this, the agent included. The item stays in force until the human rules, but it is marked, the evidence is attached, and it goes to the human with the decision it blocks.
- *Superseded*: the human replaced it (H3). It stays in the record, struck through, with what replaced it.

Questioning an item is not overriding it. The agent does not quietly build on its own correction. It does not keep building on an item it has evidence against, either. The architecture the agent proposed and the human accepted is *stated* by acceptance, and it is questioned like anything else when reality disagrees.
---

## Annotating an item

Every requirement, decision and design item in a committed artifact carries one tag, so a reader sees its weight without opening the record:

```
- Guests are charged at confirmation, never before.
  [stated · decided · 2026-10-04 · decisions.md#D-7]
- Nightly sync runs as a daily batch.
  [stated · tentative · 2026-10-04 · D-9 · questioned: vendor rate limit allows hourly (D-14)]
- Read models are rebuilt from the event log.
  [inferred · open: confirm before Phase 2]
- One canonical write path per entity.
  [standard · A22]
- ~~Store prices in the listing service.~~
  [superseded 2026-10-06 by D-12]
```

The tag is the source (H2), the firmness for a stated item, the date, and where the words are recorded, followed by the standing if it is not current. The record of the human's words is the decisions log; signed receipts of those words are local integrity records and are not committed.

## What this replaces

The standards used to speak of *signed* items and *ratified* artifacts, which presumed a signing ceremony around every requirement. That ceremony controlled nothing an agent could not route around, and it made the work slower. The authority was always the human's word. Recording that word, how firmly it was said, its date and whether it still stands is what makes it usable, so that is the mechanism. Where the runtime still signs, it signs records of the human's words with keys it minted itself (`factory init`), for integrity between workers. The human signs nothing.
