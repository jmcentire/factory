# Practice — assess the affected surfaces

Trace the observable behavior and affected dependencies before implementing a change,
and refine the assessment as evidence appears. Follow callers, consumers, accessors,
reports, jobs and public contracts beyond the edited files. The assessment's size follows
the task; a concise section in the existing brief or review can suffice.

For each affected interaction, distinguish behavior that must remain invariant from
behavior the user intends to change. State the contract, consumers and useful checks:

| Surface / interaction | Consumers | Intended behavior | Verification |
|---|---|---|---|
| Existing contract | Current callers | Preserve the public behavior | Relevant regression or existing behavioral coverage |
| Changed contract | Current and future callers | State the new behavior and any migration | New behavior plus unaffected-consumer checks |

Use [TESTING.md](../standards/TESTING.md) to choose meaningful evidence. A named artifact
or copied phrase is not a behavioral guarantee. Assess existing coverage and add checks
for the actual gaps. Independent testing derives from requested behavior and public
contracts, not the Coder's implementation.

Assess criticality by the cost of being wrong and the side effects the change can reach.
Unknown consequences need investigation and conservative treatment. Critical behavior
needs adequate independent coverage before acceptance; routine or cosmetic changes use
proportionate checks with limitations reported. Criticality and oracle adequacy are
separate questions: a green weak oracle does not establish safety.

Inspect actual permissions for any tools, credentials, network routes or integrations
needed by the work. Existing user authorization and platform-enforced restrictions govern
those actions. A local document cannot grant access or override a platform denial.

Check present consumers, future access paths and diagnostics together. A shared surface
can satisfy its own local tests while breaking another consumer. Record any migration,
known limitation or unresolved gap, and ensure an operator can diagnose concrete failures
without leaking secrets or producing noisy unbounded output.

The Validator and resident Orchestrator assess the actual candidate and its evidence
under [SOFTWARE-FACTORY.md](../SOFTWARE-FACTORY.md). Keep the assessment current instead
of treating the initial list as proof that every affected surface was found.
