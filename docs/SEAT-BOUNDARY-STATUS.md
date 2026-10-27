# Seat boundary implementation and remaining activation

These modules are local implementation components. They are not automatically installed by
`factory.sh`, and their presence does not qualify an existing or resumed provider session.
The ordinary tmux route retains its documented unqualified status until a host actually binds
and verifies these components. No additional human signature ceremony is implied.

`harness/validator_boundary.py` compiles an explicit role/path contract into a per-process
macOS kernel profile. Denied paths cannot be read through shell/interpreter or symlink access;
control records cannot be overwritten or renamed through their parent. Unix sockets are denied
except exact host-owned endpoints; ordinary IP networking and permitted files remain usable.
Sibling process inspection and signalling are denied; the process may inspect itself and stop
its children. Inherited nonstandard file descriptors and ambient credential variables are
removed before exec. Authentication-directory paths and the exported `cl` function are retained;
the child must not source a login profile again and reintroduce unrelated credentials.

The contract records role, denied-read roots, protected-write roots and allowed socket paths.
Its digest binds those fields, the generated profile and allowed environment names. Existing
hard links can escape path-based rules: metadata admission therefore refuses multiply-linked
protected files and symlinks inside protected trees. It does not read their contents. Host setup
must retain that invariant when creating later workspaces; this is not a claim against another
unconfined host process modifying the filesystem after admission.

`harness/lane_custody.py` is the host-side fixed-operation endpoint. Kernel peer PID and observed
PID/start ancestry bind a request to a seat. The service must be outside that seat's process
tree. The host supplies exact canonical entrypoints/arguments; a client supplies only operation
ID, request ID and plan digest. A durable started receipt precedes execution. An uncertain prior
request never automatically runs again. Completed responses contain status/return code, not
cross-role command output. Completing a launcher command does not mean its model work completed,
and a timeout is not a claim that all possible external effects were undone.
Host operations execute from the protected Factory checkout, with explicit target/run paths in
their admitted arguments, never a seat-writable cwd. Module dispatch uses isolated Python and
the pinned code root, preventing a target's package or PYTHONPATH from replacing host code.

`harness/signet_hooks.py` binds an installed evaluator digest, its existing owner HOME/account, a scoped
project and a per-seat policy session. It translates documented Claude/Codex/Cursor hook shapes
without altering policy or losing input redactions. It emits explicit denial for unavailable or
unrepresentable policy results. Generated settings preserve scoped Kindex and login isolation.
The nonsecret owner account is pinned because removing USER made the installed evaluator select
an unconfigured state store. Cursor's dedicated MCP hook preserves server identity; a generic
preToolUse event cannot deliver allow-side policy context, so that action remains blocked while
the context is delivered through the supported denial message. No policy effect is discarded.
Neither configuration generation nor unit tests establish actual provider hook execution.

Before live reliance, the host must:

- Protect the boundary/guard contracts, hook implementation/settings, canonical custody code and
  receipts from seat writes, and exclude opposite-role work products from the seat.
- Start custody outside the seat and bind the actual executing PID/start identity to supervision.
  A shell or mirror label is insufficient. Only the approved fixed operations may be registered.
- Verify actual provider hook loading, applicable trust review, evaluator health and a completed
  relevant Kindex call in the affected session. No fabricated prerequisite markers.
- Verify the actual subscribed CLI and model, useful tools, authenticated login, and the kernel
  boundary together. The disposable tests prove only the explicitly exercised local mechanisms.
- Verify supervisor control and required stop acknowledgement against that actual session.
  OS service/IPC escape coverage, runtime guard failure detection and automatic activation are
  not established by the tests of these modules alone.

No locked evaluator rules, global hooks, credentials or provider safety settings are changed by
these components. The historical denied script and real restricted transcripts are not test
inputs. Provider runs and the book acceptance workload remain separate from fixture validation.
