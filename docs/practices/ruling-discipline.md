# Ruling discipline: a ruling is a contract change, so make all of it

When a lane asks a question the spec cannot answer, the Validator rules. Each ruling below
cost at least one lane round because it changed the meaning without changing every place
the meaning lives. Run this checklist before a ruling goes to the Orchestrator.

## The checklist

1. **A ruling that says a value is carried, recorded or returned adds the field** to every
   contract type, storage row, migration and API schema on its path, in the same change.
2. **A ruling that names a new reason, kind or code adds the enum member** to every enum copy
   and to the published API schema, and leaves the copies equal. (A reason named in prose but
   absent from the enum blocked a round.)
3. **Nullability is part of the field.** A ruling that says "records null" must make the field
   admit null everywhere it is declared. A field described as present-null is present and null,
   never omitted, on every stored row and wire object.
4. **Verbs need seams.** "Runs in a transaction", "holds a lock across", "emits a signal",
   "reads config", "schedules", "wakes": each needs a declared seam (a service, a method, a
   config field) in the generated stubs.
5. **One home per fact.** Before assigning a fact to a field or version, search for a field that
   already owns it. Two homes for one fact (two version hashes over the same read; a secret and
   a reference to it in two config fields) become a question the moment a lane tries to be
   consistent.
6. **A superseding ruling deletes what it supersedes.** Grep the contracts, the generated stubs,
   the published API schema and the test-case suites for the old behaviour and remove or rewrite
   every occurrence. Appending the new rule beside the old text leaves a contradiction the Tester
   will write tests against. (One left-behind set of postconditions and test cases produced about
   eighty failing tests.)
7. **Ground external behaviour before ruling on it.** When a ruling depends on a third-party API,
   read its documentation first and cite it. If the behaviour is undocumented, rule that the
   operation is unsupported and route it to a manual or verified path. Do not let the
   implementation call an undocumented endpoint on a production system.
8. **Prefer the authority's answer.** When a lane asks which of two values to report, the
   component that owns the fact decides; the caller forwards it unchanged.
9. **Answer every recorded question, not only the last line.** Lanes record blockers in their
   report and ask one question at the end; read the report.

## Mechanical sweeps (run before every dispatch)

Prose review misses these; scripts do not.

- **Nullability sweep:** for every contract field whose description says present-null or null,
  the generated stub must render `| null`.
- **Config-view sweep:** every field of every component config view maps to exactly one root
  config field and environment variable.
- **Signal-catalog sweep:** every signal a contract names is a catalog row; the catalog names its
  emitter; a contract's own signal vocabulary maps onto catalog rows.
- **Enum-parity sweep:** enum copies across contracts and the published schema are equal sets.
- **Superseded-text sweep:** after a ruling changes behaviour, grep for the old behaviour's
  distinctive terms in contracts, stubs, schemas and test-case suites.

## The pipeline for every ruling

Record the ruling with its reason; apply the contract edits; validate the contracts; rebuild the
stubs and type-check them; rebuild the published schema; rebuild the focused spec; sync the spec
into both lanes and commit it there; route the ruling and the briefs to the Orchestrator; dispatch.
An amendment after the Orchestrator approved goes back to it before dispatch.

## Rulings the Validator gets wrong

Expect them. A Validator ruling is a design change made by the party that also judges the work.
Two habits catch the Validator's own errors: the cross-check in `verification-probes.md` (probe
tests against the new implementation), and reading a lane's question as a possible defect in
the ruling it cites before answering it.
