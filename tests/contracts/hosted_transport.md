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
