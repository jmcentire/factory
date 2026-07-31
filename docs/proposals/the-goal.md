The goal:

- Someone tells the factory about a thing.
- The factory pushes back to refine the ask into a well-formed Product Spec.
- The factory presents a Tech Spec/Architecture, again some pushback until mutual sign-off from human+AI.
- The factory produces a Test Plan with edge cases, tests, assertions, monitoring, alerting, error handling, etc, another agreement.
- The factory builds with the validator (the agent that's been involved in the planning) + coder&tester.
- The validator validates the build, checks the tests, resolves issues, ensures compliance with specs.
- Green-lit build shown to human user for approval in fresh environment.
- Human accepts and change enters CI/CD and out to production.
- There is a loop at play, so that if review feedback is provided (either by bot or human), the appropriate steps of the process are re-engaged.
