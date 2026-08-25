# Campaign launch

`factory launch-campaign` is the generic outer loop for an already-ratified
Factory run. It does not create a Product Specification, change a phase, choose
a target, or supply role policy. Those remain in the signed run and in the
qualified Coder, Tester, and Validator runtime commands.

The launcher accepts an operator-owned JSON contract, but refuses to use it
unless the exact bytes are part of the supplied externally pinned resume
checkpoint. The contract therefore controls bounded transport only: argv,
working directory, and time ceilings.

```json
{
  "schema_version": "factory-campaign-launch/1",
  "initial_attempt_id": "attempt-1",
  "next_attempt_prefix": "repair",
  "workdir": "/absolute/operator/workdir",
  "attempt_command": ["/absolute/path/to/attempt-driver"],
  "diagnose_command": ["/absolute/path/to/validator-diagnosis-driver"],
  "escalate_command": ["/absolute/path/to/validator-escalation-driver"],
  "validator_launch_repair_command": ["/absolute/path/to/launch-repair-driver"],
  "max_attempts": 3,
  "max_elapsed_seconds": 3600,
  "attempt_timeout_seconds": 1200,
  "diagnosis_timeout_seconds": 300
}
```

All command fields are argv arrays, not shell strings. `escalate_command` and
`validator_launch_repair_command` are optional. The command runner receives a
small transport environment:

- every command: `FACTORY_CAMPAIGN_RUN_ID` and
  `FACTORY_CAMPAIGN_CONFIG_DIGEST`;
- attempt command: `FACTORY_CAMPAIGN_ATTEMPT_ID` and the optional signed
  `FACTORY_CAMPAIGN_REPAIR_BRIEF` path;
- diagnosis/escalation command: its mode, failed attempt identity, destination
  for one repair-plan JSON object, and the public predecessor/phase digests;
- launch-repair command: failed attempt identity and a closed public launch
  failure class.

The launcher discards command stdout and stderr. A diagnosis command may
inspect Validator-authorized private material in its own environment, but it
may return only this closed document to Factory:

```json
{
  "summary": "Requirement-level causal diagnosis.",
  "actions": ["One ordered, Coder-safe action."],
  "intent_backreferences": [
    {
      "artifact_id": "architecture",
      "artifact_digest": "sha256:...",
      "item_id": "item-id",
      "intent_digest": "sha256:..."
    }
  ],
  "failure_signature": "stable-public-cluster"
}
```

Factory validates that shape, then the existing `RepairSupervisor` signs and
records the derived Repair Brief. Raw Tester assertions, streams, traces, and
oracle mechanics never cross this boundary.

Before launch, derive a new resume checkpoint whose complete configuration set
includes the campaign JSON. Then invoke the launcher with that entire set:

```bash
factory launch-campaign \
  --runs /absolute/runs \
  --run-id run-id \
  --genesis /absolute/genesis.tessera.json \
  --root-public-key PUBLIC_KEY \
  --validator-identity agent:validator \
  --validator-key /absolute/validator.key \
  --campaign-config /absolute/campaign.json \
  --campaign-config-source-name campaign \
  --config-source campaign=/absolute/campaign.json \
  --config-source other-bound-input=/absolute/other-input \
  --checkpoint /absolute/checkpoint.json \
  --checkpoint-digest sha256:... \
  --accepted-previous-checkpoint-digest sha256:...
```

The supplied `--config-source` set must exactly match the set that was bound
into the checkpoint; the command refuses substituted, missing, or additional
configuration. A blocked attempt without candidate and oracle digests is a
Validator-owned launch failure, not Coder feedback. A blocked attempt with a
candidate and sealed oracle flows through the normal signed repair policy.
