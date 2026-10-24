# Independent watchdog repair, 2026-10-24

This is direct maintenance work authorized by the owner after the failed Factory author
runs. It is not a comparison result or independent Tester acceptance. The implementer wrote
the regression tests. Failed-run artifacts remain evidence; none were promoted to acceptance.

## Corrected behavior

- A nested tool result containing `__LANE_DONE__` no longer ends the watcher. Only known agent
  response fields can make a completion claim, and a later response can retract it. The claim
  must name the slot, agree with a successful, non-signalled wrapper exit record, and be followed
  by a positive dead-pane observation and no surviving observed process identities.
  Interactive screen text cannot prove authorship; terminal lanes report verified exit only.
  Split log records are revisited by byte offset; their text is never copied into status metadata.
- A cap/stall stop request is persisted before signalling. Its status stays `stopping` until
  exit is observed. A failed signal is recorded without repeated blind signalling. Restarting
  the watcher preserves that state; lane-message refuses delivery/resume while it is pending.
  Questions and completion claims cannot clear it. The report surface includes pending stops.
- Process observations retain PID plus UTC start time, including descendants seen before
  reparenting. PID reuse does not identify an unrelated process as the old child. Missing
  process observations cannot certify exit. This observer does not signal from its PID list.
- Pending questions still suspend the ordinary inactivity clock, but do not suppress scheduled
  progress reviews or cap stopping. This preserves legitimate question handling without the
  historical question-versus-review deadlock.
- Codex initial and resumed author invocations explicitly disable the apps connector surface.
  They retain the existing scoped local Kindex configuration and `never` approval policy.
  Installed `codex exec --help` and `codex exec resume --help` document the per-invocation
  `--disable` switch. Ignoring user configuration alone did not exclude built-in connectors.

## Evidence limits and remaining dependencies

| Work | Current evidence / dependency |
|---|---|
| Completion provenance | Typed stream parsing and wrapper receipt tests; not cryptographic authentication of stdout. The run-control files still require a real access boundary. |
| Stop verification | Dead pane plus absence of **observed** identities. Polling can miss a child which forks and detaches between observations. This is not a kernel containment guarantee. |
| Other stop paths | The durable `stopping` state covers cap/stall termination and failed wrap-up delivery. Existing review-pause, normal wrap-up and supersession paths are not fully hardened by this change. |
| Actual external Validator control | Not repaired by the lane watcher. The kickoff mirror and the executing Validator remain different processes; explicit verified custody/control is still required. |
| Kindex route | Generated launch/resume argv selects the local route. A completed call from the actual model client is still required before qualification; a direct server probe is insufficient. |
| Author information separation | Still unvalidated. Projected directories are not a host access boundary. No author restart or comparison follows from these unit tests. |
| Denied bootstrap script | `canary-checks.sh` was not reconstructed, retried or executed. Its provider safety interruption is specific evidence about that operation, not a blanket restriction on these independent repairs. |
| Provider feedback | The draft was canceled without submission. Sending session/code material requires disclosure consent; it is not a dependency for independent source repairs. |

The inert bootstrap configuration is not activated by this repair. No global settings,
credentials, live Signet rules, original repository checkout, deployment or comparison run is
changed. Signet remains active, and invalid author lanes remain stopped.
