# Factory harness

The command workflow is documented in [docs/HARNESS.md](../docs/HARNESS.md);
[docs/SOFTWARE-FACTORY.md](../docs/SOFTWARE-FACTORY.md) defines role responsibilities.

```sh
harness/factory.sh my-task /path/to/task.md --repo /path/to/repository
harness/status.sh my-task
harness/endgame.sh my-task EXACT_COMMIT --repo /candidate/repository -- python -m pytest
```

`factory.sh` creates the local task record through `run_record.py create`, then starts
the selected Validator and Orchestrator through `run_record.py start`. The record retains
the exact task bytes, repository, starting commit and tree, role selections and private
run socket. `--runs PATH` selects the records directory; `--log PATH` selects optional
human-readable output; `--budget USD` sets a ceiling for explicitly metered author work.

The Validator prepares separate Coder and Tester repositories with `role_inputs.py prepare`,
then starts the assigned native conversations through `tmux_lane.sh`. Author communication
uses `tmux_lane_message.sh`; coordinator messages use durable inboxes via `inject.sh`.
Keep each author's inputs private and inspect public interface/specification selections
for implementation leakage before launch.

`endgame.sh` runs the supplied project check command against the exact candidate and
retains output and exit status. The Validator integrates independent tests, inspects their
assertions and judges the result; the resident Orchestrator examines the evidence and
raises concrete findings. Passing a check does not publish, merge or accept the task.

Credentials, platform permissions, safe Git reads, exact process/session identity and
independent author inputs remain enforced boundaries. Report concrete permission failures
and preserve evidence rather than substituting a different process, model or repository.

Run `make ship` from the repository root before claiming the complete release checks pass.
