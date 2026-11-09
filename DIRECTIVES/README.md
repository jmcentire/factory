# Task direction

The user's request and subsequent clarifications establish the task's intended behavior.
Factory retains the task in its run record and the Validator records consequential
interpretations with their source. Repository instructions and actual platform permissions
continue to apply within the user's authorized scope.

Start a task with `factory run NAME TASK --repo /path/to/repository`; see
[the workflow](../docs/SOFTWARE-FACTORY.md). The run record is maintained by
`harness/run_record.py` and does not require a project directive ledger.
