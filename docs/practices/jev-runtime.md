# Jev observations in a resident run

The dispatcher can screen activity with the 17 msg-r2 rules in `harness/jev_rules.py`.
The resident Orchestrator still reads the entire activity journal and performs the standing
checks. Jev supplies untrusted, uncalibrated observations; it never grants a transition,
clears a block, or substitutes for a missing artifact. The API contract is the
[TypeSafe API reference](https://docs.typesafe.ai/api), checked 2026-10-09.

## Select and bound it

At ignition, add `--jev-screen-calls N` to `harness/factory.sh`, where N is 1–1000.
`TYPESAFE_API_KEY` must already be in the operator environment. This explicit selection
allows bounded captured lane text and retained run artifacts to be sent to
`https://api.typesafe.ai/v1/systemone`. With no selection, metadata records `jev_screen: null`
and no provider call occurs, even when a credential is present. Selection records the exact
rulebook digest, `jev-1.13.0`, and the call ceiling. The key is transferred only to the run's
tmux session environment, never to metadata, prompts, launch command strings, or receipts;
the Orchestrator and author lanes use scrubbed environments.

Each request is at most 128 KiB, each retained artifact at most 16 KiB, and each response at
most 64 KiB. A subprocess imposes an eight-second total deadline. There are no retries or
redirects, and at most four requests per dispatcher poll. Before each call, a durable
reservation consumes one of the run's N calls. A restart never repeats a reserved call:
an interrupted call becomes unavailable. Exhaustion, outages, malformed answers, and missing
context are explicit unavailable results. They cannot manufacture a negative finding.
The call/input ceilings bound exposure and consumption; they are not a live billing meter.

`orchestrator/jev.jsonl` retains each reservation and receipt, the input state, activity and
request digests, rulebook/model configuration, raw probabilities, and band decisions.
The channel uses bounded regular-file journals and rejects corrupt or linked journal paths.
Activity is never filtered by the screen. A lagging screen exposes a completed cursor prefix;
the Orchestrator can assess that prefix while the remaining activity stays visible.

## Supply the evidence a rule needs

Pane records supply only `lane`, `pane_excerpt`, `pane_tail`, and `line`.
`TASK.md` supplies `run_brief`, and `artifacts/testing-strategy.md` supplies `testing_strategy`.
No pane is relabelled as a contract, test file, report, or brief.

For a multi-artifact review, the Validator retains exact copies beneath the run's `artifacts/`
directory and appends a checkpoint with `orchestrator_channel.py append --snapshot-file FILE`.
That file contains a JSON object of evidence handles, for example:

```json
{"jev_evidence":{"brief":"artifacts/round-2-tester-brief.txt","judge_flags":"artifacts/judge-flags.txt","ruling":"artifacts/ruling-12.txt","contract_excerpt":"artifacts/contract-12.txt","lane_report":"artifacts/tester-report.txt","test_excerpt":"artifacts/tester-case.txt","validator_access":"artifacts/validator-access.txt"}}
```

Use `--kind pre_dispatch --source validator --detail 'Review exact round-2 evidence'` with
that snapshot. A retained path is evidence for review, not authorization. Absolute paths,
parent traversal, symlinks (including parent directories), non-regular files, empty files,
oversized sources, and invalid UTF-8 cannot supply evidence. Missing fields mark the affected
rules unavailable. The Orchestrator must inspect those checks manually, obtain the missing
artifacts, or block; it may not interpret unavailable as compliant.

## Consume the receipt

Enabled runs use `factory-orchestrator-assessment/4`: all assessment/3 fields plus
`screen_review`. Include one entry for every new activity cursor through the assessment's
`through_cursor`, in order, referencing its exact receipt digest:

```json
"screen_review": [{
  "cursor": 7,
  "receipt_digest": "sha256:<the retained receipt digest>",
  "rules": {
    "model_unqualified": {"decision":"block","basis":"No passing qualification receipt exists for this model and role."},
    "unavailable": {"decision":"dismiss","basis":"Manually checked the missing artifact comparisons against the retained round-2 files; cite paths and conclusions here."}
  }
}]
```

Every `yes` (>0.7) and `escalate` (0.3–0.7 inclusive) rule requires its own disposition and
bounded evidence basis. `no` (<0.3) grants nothing. Missing evidence and provider failures are
grouped under one `unavailable` review per occurrence instead of seventeen false findings.
A dismissal is the Orchestrator's manual judgment, not a Jev clearance. An upheld finding
requires an adherence finding and `block` or `halt`; the existing Validator/human disposition
mechanism remains the only route to resolve a block. Reports that omit, replace, or skip
receipts are refused, including at transition-time revalidation. Exact report retries remain
idempotent. Disabled runs retain assessment/3 and manual checks.

These receipts prove delivery and review recording, not that a model understood a document.
Tester reports must still cite the T-rules and demonstrate their application in the tests.
