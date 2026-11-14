# Independent hosted-kernel primitive qualification

Public acceptance outline from Agy, 2026-11-14, G0/K1-K4. Runtime baseline
b5cbf82 (merged PR117). Tests read public CLI help and existing public contract
test API; no runtime implementation inspected. No provider/model calls.

- G0 is run first: explicit boundary-protected public hosted launches must refuse77
  without even preflight spawning a stand-in. This is **blocked hosted admission**,
  not a successful positive hosted-kernel launch. No required flag is disabled.
- Standalone validator_boundary public CLI then executes deterministic Python and
  shell subprocess probes. Denied-read sentinel is unreadable and unlistable;
  protected overwrite/create/rename/unlink fail; original control hash is intact.
- Baseline identical interpreter and cat outside boundary successfully read the
  synthetic denied file, establishing negative-control sensitivity.
- Workspace output and synthetic session persistence remain usable. A fresh guarded
  process reopens exact synthetic session identity/history. This is process
  inheritance/persistence only, **not actual provider session recovery**.
- Mismatched host-pinned digest refuses before child execution, no plain exec fallback.

These results do not establish hosted tool authorization, Signet behavior,
all-tool interception, native credential compatibility, or provider config safety.
Required hosted protections continue to refuse. Real provider hooks or model/tool
calls are not part of these tests. No global profile or native settings changed.

Extension on b5cbf82: static synthetic UNIX allowlist plus fake global-Kindex and
provider-settings tests pass. Both unwrapped sockets connect; inside boundary only
the declared broker connects. Fake global store reads/list and all tested writes
fail; fake provider settings writes fail and bytes remain identical. This is not
proof about real provider state-layout compatibility or remote Kindex transports.

`test_hosted_kernel_future.py` is a separately committed future-feature oracle,
intentionally red on b5cbf82: 4 public initial admissions refuse77 before provider
spawn. It specifies desired kernel-only launch/live followup/same-session restart
and tampered retained contract refusal, while preserving required semantic
Signet/custody refusal. Host state is protected from provider writes. It is not
qualified until actual candidate succeeds and independent review approves the
explicit kernel-only admission change. No required protection flags are unset.
