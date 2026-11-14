# Hosted transport acceptance, bounded Slice 1

Authority: Jeremy permitted protocol hosting while preserving subscription and
central Signet; Agy orchestration contract dated 2026-11-14, base main7001e274.
Independent tests are authored before candidate implementation, from public CLI,
MSP exported schema and observed ACP protocol, not coder implementation.

- Muse uses serve/MSP; AGY uses acp/stdio. No print/exec, API-key injection,
  saved-settings edits, project hook pollution or hidden provider state reads.
- Required Signet or author/policy custody refuses77 BEFORE any provider process,
  including preflight. This slice does not qualify security enforcement.
- Unmanaged lane_agent transport tests are not functional protected Factory
  acceptance. Public prepared author launch separately tests refusal.
- Exact session resume only; AGY load can accept missing identity, so never use
  load as fallback. Missing/error/mismatched identity fails70 before prompt.
- Initial launch and message handovers persist before protocol write. Acceptance,
  observation and completion are distinct. Ambiguous write never silently replays.
- Muse messages use durable UUIDv7 command IDs and ifBusy queue. ACP maintains
  single outstanding turn; no second prompt before prior response finishes.
- IPC validates exact bound session; wrong-session submission sends no prompt.
- Operator permission request is visible/held, unknown actions not autoallowed,
  persistent grants never synthesized. Prompt hold blocks new delivery.
- Assistant done text remains observable in wrapper output. Same-session followup
  preserves initial history without resending initial prompt.

Real-provider evidence only qualifies lifecycle/transport so far. Echo or fake
providers cannot prove all-tool Signet interception or pre-read role isolation.
Muse hook/declare currently returns methodNotFound despite experimental schema.
Native callback errors/timeouts may fail open: security refusal remains required.

Adversarial additions: ACP config/current-mode updates cannot settle a rejected
prompt; missing/non-string/foreign callback identity cannot settle it either.
Requested model/effort must be selected in returned config options before prompt.
Corrupt or unreadable retained Muse command ledger cannot create fresh command IDs
or silently replay. Public restart requires provider resume argv as well as retained
session file and launch message ID (Muse resume UUID, AGY --conversation UUID).

## Historical terminal-end-to-end migration (hosted default authorized)

The old terminal path has been retired for AGY/Muse. Its eleven end-to-end cases
remain explicitly historical/skipped, not silently green. Callable hook/parser
unit contracts elsewhere are retained unchanged. No protected security rule is
relaxed: explicit required custody still refuses before any provider spawn.

| Historical case | Current executable hosted replacement |
| --- | --- |
| AGY native launch observation/dedup | `test_hosted_initial_and_live_followup_bind_exact_identity_without_pollution` (literal shell text, exact identity, retry dedup, untouched HOME/project) and `test_followup_uncertain_write_is_durable_and_not_replayed` (pre-write intent) |
| AGY Stop cannot release held followup | The old manual-entry-only policy is intentionally replaced by hosted delivery. `test_active_turn_respects_native_queue_or_acp_busy_boundary` preserves outstanding-turn ordering; `test_pending_unknown_permission_is_visible_and_never_autoapproved` preserves genuine permission holds. Stop is not a delivery receipt. |
| AGY lost reply, stale/missing/wrong-slot receipt (3 cases) | `test_hosted_lost_reply_and_damaged_process_custody_never_replay` (both providers x3), plus wrong-session rejection in live-followup test |
| AGY exited child, observed initial | `test_completed_hosted_child_cannot_replay_initial_on_restart` and strict ended-session resume success/missing/mismatch tests |
| AGY exited child, uncertain initial | `test_uncertain_initial_handover_never_replays`; fresh retry cannot resend initial |
| Manual exact held receipt (both providers) | Automatic host receipts replace manual hooks; live-followup exact identity plus pending-permission hold test. No autoapproval is introduced. |
| Old receipt vs new identical body (both providers) | `test_identical_hosted_bodies_remain_distinct_deliveries` (unique intent/command ID), `test_acp_unrelated_update_then_rejection_is_not_prompt_receipt` including same-session prior-turn content/config/mode and missing/foreign/nonstring identity. Existing Muse exact command-ID binding is checked by durable wire hash linkage. |

Synthetic tests prove adapter protocol behavior, not provider honesty or Signet
all-tool enforcement. Installed Muse echo and one authorized AGY text-only
subscription canary additionally exercise real provider transport; neither
qualifies protected read isolation. Unknown subscription allowance is not a
claim of free or unlimited capacity.

`test_muse_old_command_event_cannot_settle_rejected_new_message` additionally
injects a prior command's same-session turn-start while the new turn is rejected;
it must not settle the new message. This explicitly replaces old manual-hook
stale-event protection for MSP rather than relying on identical-body coverage.
