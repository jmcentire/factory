# The goal

> **Status: unratified proposal.** This records a design for review; it does not amend doctrine or
> authorize implementation.

- Someone tells the factory about a thing.
- The factory pushes back to refine the ask into a well-formed Product Spec.
- The factory presents a Tech Spec/Architecture, again with pushback until human approval and an independent Validator attestation.
- The factory produces a Test Plan with edge cases, tests, assertions, monitoring, alerting, error handling, etc, another agreement.
- The factory builds with the Validator (the agent that's been involved in the planning) plus Coder and Tester.
- The Validator validates the build, checks the tests, resolves issues, ensures compliance with specs.
- Green-lit build shown to human user for approval in fresh environment.
- Human accepts and change enters CI/CD and out to production.
