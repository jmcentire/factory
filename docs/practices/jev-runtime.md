# Optional Jev observations

The Jev screen supplies bounded semantic observations to a resident Orchestrator.
Use it only when explicitly selected and authorized to send the relevant activity to
the configured service. A credential's presence is not permission to send task data.

The Orchestrator continues to inspect the work and retains responsibility for concrete
findings. Screen results are untrusted evidence; they do not approve models, authorize
a transition, accept a candidate or replace independent testing. Missing context, an
unavailable provider or an exhausted call ceiling is a visible limitation.

The screen implementation in `harness/jev_screen.py` retains request configuration,
reservations and observations for inspection. Inspect its actual result and report the
limits of the observed run. An observation or recorded disposition does not prove
that a model understood the task.

Ordinary startup, author work and validation follow
[SOFTWARE-FACTORY.md](../SOFTWARE-FACTORY.md); screen selection is optional.
