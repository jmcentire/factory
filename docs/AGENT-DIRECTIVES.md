# The Software Factory — Role Directive Entry Point

The authoritative, self-contained role directives now live in
[`SOFTWARE-FACTORY.md`, Part II](./SOFTWARE-FACTORY.md#part-ii--role-directives).
They are kept with the system specification so the shared foundation, role topology, and
directives cannot drift as separate documents.

Use these direct links:

- [Shared foundation](./SOFTWARE-FACTORY.md#shared-foundation)
- [Directive — Validator](./SOFTWARE-FACTORY.md#directive--validator)
- [Directive — Coder](./SOFTWARE-FACTORY.md#directive--coder)
- [Directive — Tester](./SOFTWARE-FACTORY.md#directive--tester)
- [Phase and role map](./SOFTWARE-FACTORY.md#appendix-a--phase-and-role-map)

The factory has exactly three roles: **Validator, Coder, Tester**. The Coder and Tester share
the same signed spec and have no channel to each other. The Validator coordinates with the
human and both roles, runs the tests, and judges.

This file is a compatibility entry point, not a second copy of the directives. Do not add
directive language here. Edit the canonical working specification under the human gate.

The Validator's additional process-completeness and release-control obligations are in
[`VALIDATION-DIRECTIVE.md`](./VALIDATION-DIRECTIVE.md). That operational supplement does not
change the role or the doctrine.
