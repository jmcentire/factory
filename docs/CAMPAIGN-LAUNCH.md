# Repair campaign core

`CampaignLauncher` is Factory's generic retry-policy core for an
already-ratified run. It does not create a Product Specification, change a
phase, choose a target, or supply role policy. It also does **not** invoke an
operator-provided executable.

Its checkpoint-bound `factory-campaign-launch/2` configuration is deliberately
small:

```json
{
  "schema_version": "factory-campaign-launch/2",
  "initial_attempt_id": "attempt-1",
  "next_attempt_prefix": "repair",
  "max_attempts": 3,
  "max_elapsed_seconds": 3600
}
```

The caller must provide two Factory-owned typed interfaces:

- `AttemptExecutor` executes one immutable attempt from a separately
  checkpoint-bound `factory-attempt/1` contract.
- `ValidatorDiagnosisProvider` performs privileged diagnosis and returns a
  closed `RepairPlan`. Factory signs and records the resulting Repair Brief;
  raw Tester assertions, streams, traces, and oracle mechanics never cross to
  Coder.

The public CLI is intentionally not exposed until the typed attempt and
diagnosis adapters are complete. Reintroducing the old argv transport would
misrepresent an arbitrary host process as a Factory-qualified attempt.
