# Typed attempt execution

`factory validate-attempt-config` validates the closed
`factory-attempt/1` configuration before a run. `factory execute-attempt`
then invokes the Factory orchestrator directly; it never launches a
campaign-provided program or derives authority from environment variables.

Every path in the configuration is a symbolic configuration-source name. Pass
the complete source set with `--config-source NAME=PATH`; the execution
command also verifies that exact set against the externally pinned resume
checkpoint.

The role records contain an enrolled identity, an executable source name,
literal arguments, and the explicit trusted source names. They are Factory
attempt inputs, not a generic command hook:

```json
{
  "schema_version": "factory-attempt/1",
  "artifacts": {
    "target_manifest": "target",
    "pattern_catalog": "catalog",
    "build_plan": "plan",
    "acceptance_catalog": "acceptance",
    "acceptance_catalog_human_receipt": "acceptance-human",
    "acceptance_catalog_validator_receipt": "acceptance-validator"
  },
  "roles": {
    "coder": {
      "identity": "agent:coder",
      "executable_source": "coder-runner",
      "arguments": [],
      "trusted_path_sources": ["coder-runner"]
    },
    "tester": {
      "identity": "agent:tester",
      "executable_source": "tester-runner",
      "arguments": [],
      "trusted_path_sources": ["tester-runner"]
    },
    "validator": {
      "identity": "agent:validator",
      "executable_source": "validator-runner",
      "arguments": [],
      "trusted_path_sources": ["validator-runner"]
    }
  },
  "prebuilt_author_outputs": null,
  "surface_evidence": [],
  "determinism_records": [],
  "lane": "capability",
  "independence": {},
  "monitors": [],
  "monitor_declared_unit_count": 0
}
```

## Runner-backed author outputs

The direct author command form is for a deterministic, already-qualified lane
tool. It is not a valid way to invoke a networked model: `IsolatedBuildLoop`
correctly denies network access to Coder and Tester processes. A model-backed
attempt therefore has a distinct, Factory-owned composition:

1. `run-model` executes Coder and Tester through the qualified networked
   runner using the bounded path-free projection.
2. Each runner result is persisted as a handoff, runner receipt, and state
   capsule, then its signed broker request publishes one sealed author tree.
3. The normal build loop copies those regular-file trees into fresh Coder and
   Tester lanes, freezes them, and runs the deterministic Validator. It never
   re-runs a model inside the network-denied sandbox.

The third step accepts both sealed artifacts together and rejects any attempt
that also supplies direct Coder or Tester commands. This keeps the existing
Validator evidence and oracle-isolation guarantees while preventing an outer
networked runner from becoming a hidden shared lane.

Set `prebuilt_author_outputs` to `{ "coder": "source-name", "tester":
"source-name" }` when those two names resolve to the sealed author directories
published by `execute-broker-handoff`.  The typed executor then omits the
direct Coder and Tester commands entirely; Validator remains the only process
launched inside the deterministic build loop.

A repair brief remains an explicit `--repair-brief` input. A separate
Validator diagnosis adapter is required before the retry supervisor can be
re-exposed as a public command; this command deliberately executes one
immutable attempt.
