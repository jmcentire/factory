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
  process observations cannot certify exit. The follow-up stop path revalidates each identity
  immediately before an individual signal, never signalling a process group.
- Pending questions still suspend the ordinary inactivity clock, but do not suppress scheduled
  progress reviews or cap stopping. This preserves legitimate question handling without the
  historical question-versus-review deadlock.
- Codex initial and resumed author invocations explicitly disable the apps connector surface.
  They retain the existing scoped local Kindex configuration and `never` approval policy.
  Installed `codex exec --help` and `codex exec resume --help` document the per-invocation
  `--disable` switch. Ignoring user configuration alone did not exclude built-in connectors.

## Follow-up: process custody and other stop paths

The first increment is commit `39795a1` (2109 passed, 22 skipped in its full ship run).
This follow-up extends the same maintenance scope; it does not retroactively validate failed
author runs or activate controls in their live sessions.

- Kickoff ignition binds the supplied external Validator PID/start to an exact tmux pane.
  The binding includes pane process start identity and cannot be replaced in place. Supervision
  targets that executing process, not its mirror. Missing binding, stale identity, unknown
  inventory or unresolved control prevents an acknowledgement and author admission.
- Stop intent and per-identity signal attempts are persisted before signals. A locked control
  transaction retains known descendants across reparenting and supervisor restart. SIGSTOP
  and SIGCONT acknowledge only observed stopped/running state (or verified identity exit).
  Failed or uncertain attempts are not blindly repeated. Holds remain visible until resolved;
  delivery failure does not erase a stop. Notification throttling does not suppress observation.
- Review pause and wrap-up use the durable `stopping` path. A review is not called paused and
  a wrap-up message is not sent until pane/tree exit is verified. Lost exit evidence returns
  to pending, never ordinary work. Supersession and lane-message continuation reject pending
  or unverified stops. A failed close cannot be ignored by the launcher.
- Fixtures cover PID reuse, orphan retention, unavailable observations, signal/delivery
  failure, restart without duplicate signalling, pending stop/resume, and stopped-lane
  continuation. A disposable local process demonstrates actual stop/resume acknowledgements;
  live control seats are never demonstration targets.

## Evidence limits and remaining dependencies

| Work | Current evidence / dependency |
|---|---|
| Completion provenance | Typed stream parsing and wrapper receipt tests; not cryptographic authentication of stdout. The run-control files still require a real access boundary. |
| Stop verification | Dead pane plus absence of **observed** identities. Polling can miss a child which forks and detaches between observations. This is not a kernel containment guarantee. |
| Other stop paths | Review, wrap-up, supersession and lag/control now require observed acknowledgement; source/fixture validation does not activate this code in existing live runs. |
| Actual external Validator control | Explicit PID/start/pane binding and individual verified signals. POSIX `kill` is not an atomic process handle; `ps lstart` has second precision. A check-to-signal race and unobserved detached children remain limitations. Binding proves custody, not model identity or an information-access boundary. |
| Kindex route | Separate actual Codex client qualification completed local scoped write/read and resumed search/read. A stale MCP graph reference first failed and is retained as evidence; supported scoped search refreshed it. Diagnostic tool restrictions and an empty synthetic repository mean this is local Kindex usability, not author isolation. |
| Author information separation | Still unvalidated. Projected directories are not a host access boundary. No author restart or comparison follows from these unit tests. |
| Denied bootstrap script | `canary-checks.sh` was not reconstructed, retried or executed. Its provider safety interruption is specific evidence about that operation, not a blanket restriction on these independent repairs. |
| Provider feedback | The draft was canceled without submission. Sending session/code material requires disclosure consent; it is not a dependency for independent source repairs. |

The inert bootstrap configuration is not activated by this repair. No global settings,
credentials, live Signet rules, original repository checkout, deployment or comparison run is
changed. Signet remains active, and invalid author lanes remain stopped.
