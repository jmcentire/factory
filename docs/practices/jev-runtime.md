# System One observations

The resident Orchestrator's ordinary `dispatcher.py --observe` invocation screens new
lane output using the current questions in `harness/jev_rules.py`. One bounded request
covers a batch, and replaying the exact batch reuses its retained observation. The pure
`observe()` reader still returns original activity without side effects. Screening failures
leave that activity visible and report unavailability; scores never stop a process or grant
permission.

The 24 current questions cover role boundaries, fidelity to user intent, unsupported access
and authority claims, evidence quality, concrete review findings, selected models, billing
and unnecessary session replacement. Historical source labels no longer exclude useful
questions. Obsolete model qualification, signature, dispatch approval, mandatory registers
and universal baseline campaigns are not current questions. Descriptive lane state remains
separate because the bounded semantic client expects yes/no probabilities.

Inputs are bounded observed excerpts plus actual retained launch, billing, session and Tester
brief facts. Launch history is not proof of current liveness or complete user authorization.
Launch/session metadata uses allowlisted facts; environment dictionaries, transport tokens,
command arguments and private author paths are excluded from that projection. Authorized raw
activity excerpts and explicit artifact text are sent as text; this is not general-purpose
secret redaction. The isolated profile key is never included in those inputs. Generic lane prose is not relabeled
as test code, an API contract or completed review. Each rule reports which observed sources
had its required fields and which remain uncovered. A low probability does not cover missing
context or prove the current candidate is correct.

For richer existing evidence, the Validator can use the ordinary Orchestrator inbox message
with JSON text such as `{"jev_evidence":{"test_excerpt":"artifacts/test.txt",
"behavior_contract":"artifacts/behavior.txt"}}`. These are handles to actual bounded files
under the run, not a required manifest or new approval artifact. The observer reads complete
messages, refuses linked evidence and reports absent context as uncovered. Available fields
are those declared by current questions; no extra document must be created merely to satisfy
a field. Supply excerpts only when they are meaningful evidence for the current work.

The run retains explicit profile permission from `[system_one] profile = true` in the normal
Factory configuration, with `FACTORY_SYSTEM_ONE_PROFILE=1` or `0` taking precedence at creation.
The default is false. Startup, resume and checkpoints use the retained choice. Only the
existing bounded HTTP child sources the authorized profile and returns validated observations;
provider keys do not enter agent or candidate environments. Without a usable key or service,
checks are unavailable and ordinary work continues. No profile edit or automatic global
profile sourcing is introduced.

Artifact attribution also runs at endgame and after the ordinary `run_record.py pr` create/edit
helper. The helper performs one authorized mutation, then fetches the actual PR head/body and
retains the check. A completed mutation with unavailable verification is rechecked read-only,
never repeated as another create. An external tool or push requires a fresh explicit
`run_record.py provenance ... --pr URL` check; this is not a GitHub webhook or interception of
all external commands. Endgame alone leaves PR coverage not-checked.

Observations live in existing run evidence: artifact result files and
`orchestrator/system-one/`. A reserved but interrupted observation remains unavailable rather
than causing duplicate requests. The Orchestrator reads the actual evidence and sends a
concrete, report-linked assessment and next action through the existing Validator inbox.
Native acknowledgment means delivery, not completed review. Its actual response is the
behavioral evidence of review; no worker, disposition schema, approval or release gate is added.
