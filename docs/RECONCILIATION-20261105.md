# Reconciliation with current main

This combines the live-tested cleanup at `02ab42c` with main at `2e424da`
(Shane's PR #76), under Jeremy's instruction to reconcile, merge and install.

- Keep Shane's scheduler distinction: macOS `application.<bundle>.<n>.<n>`
  launch entries are GUI instances, not timers. Unknown scheduled jobs still
  fail the historical audit. This audit is not a normal task startup gate.
- Keep recognition of Claude's bypass-permissions dialog, including the newer
  trust screen whose selected answer is “No, exit”. Record a persistent prompt
  for the run's owners without accepting it. Native permission flags follow the
  user's selection; a screen scraper does not decide permissions.
- Do not restore pane-command allowlists or keystroke message injection for
  version-number and Nix wrapper names. Durable coordinator inboxes and typed
  author transports no longer infer a safe destination from those names.
- Restore explicit delivery reporting: exit 75 is busy/not delivered and exit
  77 is refused/not delivered. A queue append is not an acknowledgment. Route
  genuine user questions where the user can see them.
- Restore Shane's `FactoryRun` PR-body/commit attribution, also for Tester
  commits. It is searchable provenance, not approval or an acceptance gate.
- Preserve Alex's useful role-selection intent and adapter work. Mandatory
  profiles were already deliberately removed by Jeremy in `f9b3167` before
  this cleanup. Explicit role choices override optional local defaults and
  shipped defaults; resumes preserve the selected model. No profile prompt,
  model allowlist or mandatory named model is restored.

The separate open PR #77 (Akash's existing-target preparation/native-author
mode) was inspected, not merged. It extends the older qualification workflow;
its mandatory catalog and native-mode closure restrictions are not prerequisites
for this normal workflow. Its branch and evidence remain intact for separate
assessment. No release/version bump is part of this reconciliation.

Validation includes actual scheduler-discovery output with simulated OS
commands, positive unknown-timer controls, dialog observations without answer
requests, existing delivery/refusal and shell-history regressions, model-choice
and native startup checks, and the complete repository release-check suite.
Exact execution results belong to the retained task evidence, not this plan.
