# System One observations in a normal run

System One is an advisory host worker, separate from the resident Orchestrator.
It uses the same retained-log reader as `dispatcher.py --observe`; the normal
observer remains fast, read-only and independent of provider availability.
The worker never accepts work, clears findings, stops agents or imposes a review gate.

## Selection and credentials

New runs accept `--jev-screen auto|on|off` (or `FACTORY_JEV_SCREEN`, default `auto`)
and `--jev-screen-calls N` (1–1000, default 1000). Auto selects screening only when
the launch environment contains `TYPESAFE_API_KEY` or `JMC_JEV_API_KEY`. On selects
it even without a key, in which case calls are reported unavailable. Off sends
nothing. Selection, pinned model and rulebook digest are retained in `harness.json`
under `system_one`, never the credential. Existing runs without that field stay
disabled; they are not silently migrated or restarted.

The private tmux server's host environment supplies the provider key. Coordinators
and authors keep their existing scrubbed environments; no key is put into prompts,
command arguments or run metadata. If a retained run starts under a host without its
key, screening is unavailable rather than silently claiming success.

Screening transmits bounded captured lane text to TypeSafe's
`https://api.typesafe.ai/v1/systemone`. Do not enable it for material that is not
authorized for that destination. Source text and provider scores remain untrusted.

## Observation, cost and recovery

The host worker screens at most one new log chunk per tick, then waits two seconds.
Each source chunk is approximately 16 KiB; requests are bounded to 128 KiB and
responses to 64 KiB. The reused HTTP helper enforces an eight-second total deadline,
no redirects and no retries. Network latency never blocks ordinary observation or
author work. The configured call ceiling bounds consumption, not a live dollar meter.

`system-one/` retains exact source byte ranges, identity and digest, inputs,
configuration, reservations and receipts. A reservation is durable before a call;
recovery never retries an interrupted request. Repeated observation does not spend
again. Interrupted calls, provider failures and exhausted budgets are explicitly
unavailable, never negative findings. Pending notification can be retried without
repeating the API call; a crash at the inbox-write boundary may duplicate a notice
with the same observation ID. Original logs are always retained independently.

The worker ignores coordinator inboxes and Orchestrator output, avoiding feedback
from its own notices. It reads Validator and author logs. It stops when the run's
existing inbox-stop marker is set or its tmux session ends. No running session is
restarted to activate the feature.

## Judgment and limits

High and uncertain findings, plus unavailable observations, are queued to the
existing Orchestrator inbox with exact retained evidence. Low scores are retained
but do not clear any finding. The Orchestrator must inspect the evidence, distinguish
claims from demonstrated violations, and send concrete findings to the Validator.
Delivery is not proof of understanding or compliance.

The normal rule set checks input contamination, Validator implementation authorship,
interactive-auth stalls, presumed access, unnecessary fresh sessions or duplicate
roles, paid overage, and weak/self-serving test evidence visible in lane output.
It does not reintroduce obsolete model qualification, signature, cadence or
pre-dispatch approval requirements. Log excerpts cannot prove unobserved file
access or test quality; actual code/assertion review remains necessary.

The older `jev_screen.py` activity-journal/assessment APIs remain for historical
runs. Their metadata and mandatory receipt-assessment protocol are not the normal
workflow. Prototype scores in the proposal are not qualification of this worker or
proof that its semantic classifications are correct.
