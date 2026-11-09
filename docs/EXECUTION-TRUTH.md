# Execution truth

The current workflow uses `factory run NAME TASK --repo PATH`, implemented by
`harness/factory.sh` and `harness/run_record.py`. The explicit repository and task are
the startup inputs. Actual filesystem, credential and tool permissions remain those
of the invoking platform.

## Retained task identity

The run records the supplied task bytes, canonical repository path, starting commit
and tree, selected role settings, and private tmux socket. Creating the record does not
change the user's checkout. Existing records are never silently overwritten or adopted
as a different task. Durable inputs, messages, outputs and validation results remain
available even when optional human-readable logging is disabled.

## Execution and evidence

Startup creates resident Validator and Orchestrator conversations using the selected
native CLIs. Each seat must register its actual process before startup is reported
successful. A failed start retains its record and concrete failure so it can be retried
on that same record.

The Validator prepares asymmetric author inputs: source without withheld tests for the
Coder, selected public contracts/specifications for the Tester. Fresh Git histories and
private memory prevent omitted inputs from leaking through shared history. The Validator
must inspect semantic content; filenames and hashes cannot establish independence.

Author messages continue the retained native conversation after checking session and
process identity. Coordinator messages are durable inbox entries. Queuing a message
proves transport, not acknowledgment or substantive review.

Candidate validation materializes the named Git commit without executing repository hooks,
filters, replacement objects or network downloads, then runs the supplied project command.
It retains exact output and exit status and sends the result to the resident Orchestrator.
The Validator accepts or rejects the work after evaluating independent evidence. The
command does not grant publication, merge, installation or deployment authority.

## Operational boundaries

Inspect and stop only the intended run-owned processes. Preserve unrelated checkouts,
branches, stashes, files, applications and scheduled jobs. Cleanup inventories recorded
resources and applies changes only within its explicitly authorized scope; see the
storage workflow (see Git history).

[HARNESS.md](HARNESS.md) contains the command reference and failure behavior;
[SOFTWARE-FACTORY.md](SOFTWARE-FACTORY.md) defines the role and acceptance responsibilities.
