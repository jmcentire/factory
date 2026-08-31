"""Receipted repair ceremonies — the only exits from a wedge (plan 4.2).

Two operator commands share one shape: ``factory chain-repair`` (the R5 receipt
chain wedge) and ``factory ledger-unlock`` (the sentinel-guard wedge). Both
CONSUME a pre-signed adjudication envelope rather than minting one — the 4.1a
trust-root rail means enrolled-human keys never enter the host process, so the
operator signs the adjudication out-of-band with tessera and the host only
VERIFIES (signer must be an enrolled human from the operator-owned genesis) and
applies. Recovery is one bounded operator action per path; the applied repair
leaves a verifiable state AND a persisted signed record — degrade, never wedge,
and recovery is never silence.

Design constraints inherited from the plan: the repair authority never lives in
the file being repaired (an in-chain quarantine record could equally suppress an
honest RED receipt); the adjudication is digest-bound to the exact offending
entry; nothing is destroyed — the quarantined suffix is preserved beside the
chain, and a quarantine without its matching signed record makes the chain
refuse (unreceipted surgery is itself a wedge).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_runtime.authority import AuthorityPolicy, human_public_keys
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.tessera import TesseraCli, TesseraVerificationError


class RepairCeremonyError(ValueError):
    """A repair could not be verified and applied without guessing."""


@dataclass(frozen=True)
class _AppliedRepair:
    record_path: Path
    detail: str


def _verify_adjudication(
    envelope_path: str | Path,
    *,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
    expected_kind: str,
) -> Any:
    """Verify an operator-signed adjudication against the enrolled-human keys.

    Verification only — the host never holds the signing key (4.1a). A signer
    outside the genesis's enrolled humans is refused; so is a wrong kind.
    """
    trusted = tuple(sorted(human_public_keys(policy)))
    if not trusted:
        raise RepairCeremonyError(
            "no enrolled human exists in the trust root — nothing can adjudicate"
        )
    try:
        return tessera.verify_json(
            envelope_path,
            trusted_public_keys=trusted,
            expected_kind=expected_kind,
        )
    except TesseraVerificationError as exc:
        raise RepairCeremonyError(f"adjudication refused: {exc}") from exc


def apply_chain_repair(
    harness_root: str | Path,
    *,
    record_path: str | Path,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
) -> _AppliedRepair:
    """Apply a signed chain-repair adjudication: quarantine the offending suffix.

    The adjudication payload names the exact ``offending_entry_hash``. The chain
    file's suffix from that entry onward moves to a preserved quarantine file,
    and the signed record installs beside it at
    ``receipts/repairs/<offending-hash>.tessera.json`` — the license _load_chain
    requires before it will accept the shortened chain. Nothing is destroyed;
    appends after the repair chain from the surviving tail and still ground.
    """
    root = Path(harness_root)
    chain_path = root / "receipts" / "chain.jsonl"
    if not chain_path.is_file():
        raise RepairCeremonyError(f"no receipt chain exists at {chain_path}")
    envelope = _verify_adjudication(
        record_path, policy=policy, tessera=tessera, expected_kind="factory-chain-repair"
    )
    payload = envelope.payload
    offending = str(payload.get("offending_entry_hash", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not offending or not reason:
        raise RepairCeremonyError(
            "adjudication must name offending_entry_hash and reason"
        )
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    offense_index: int | None = None
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            offense_index = index  # an unparseable row is quarantinable by position hash
            continue
        if isinstance(row, dict) and str(row.get("hash", "")) == offending:
            offense_index = index
            break
    if offense_index is None:
        raise RepairCeremonyError(
            f"adjudicated entry {offending!r} is not in the chain — a repair must "
            f"name the exact offense, never guess"
        )
    repairs_dir = root / "receipts" / "repairs"
    repairs_dir.mkdir(parents=True, exist_ok=True)
    quarantine_path = root / "receipts" / f"quarantine-{offending}.jsonl"
    if quarantine_path.exists():
        raise RepairCeremonyError(f"quarantine already exists: {quarantine_path}")
    installed_record = repairs_dir / f"{offending}.tessera.json"
    if installed_record.exists():
        raise RepairCeremonyError(f"repair record already installed: {installed_record}")
    quarantined = lines[offense_index:]
    surviving = lines[:offense_index]
    quarantine_path.write_text(
        "\n".join(quarantined) + ("\n" if quarantined else ""), encoding="utf-8"
    )
    installed_record.write_bytes(Path(record_path).read_bytes())
    chain_path.write_text(
        "\n".join(surviving) + ("\n" if surviving else ""), encoding="utf-8"
    )
    try:
        fsync_directory_chain(repairs_dir, through=root)
    except DurabilityError as exc:
        raise RepairCeremonyError(str(exc)) from exc
    return _AppliedRepair(
        record_path=installed_record,
        detail=(
            f"quarantined {len(quarantined)} entr{'y' if len(quarantined) == 1 else 'ies'} "
            f"from {offending}; {len(surviving)} surviving"
        ),
    )


def require_quarantine_license(receipts_dir: Path) -> None:
    """Refuse unreceipted chain surgery (consumed by the chain loader).

    Every ``quarantine-<hash>.jsonl`` must have its matching installed signed
    record at ``repairs/<hash>.tessera.json``. Structural at load time — the
    signature itself was verified when the repair was APPLIED, and the
    postmortem re-verifies it; what the loader owns is that a shortened chain
    without its license never grounds a decision.
    """
    for quarantine in sorted(receipts_dir.glob("quarantine-*.jsonl")):
        offending = quarantine.name[len("quarantine-") : -len(".jsonl")]
        record = receipts_dir / "repairs" / f"{offending}.tessera.json"
        if not record.is_file():
            raise RepairCeremonyError(
                f"quarantine {quarantine.name} has no installed signed repair "
                f"record — unreceipted chain surgery is itself a wedge"
            )


def apply_ledger_unlock(
    runs_root: str | Path,
    run_id: str,
    *,
    guard_name: str,
    record_path: str | Path,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
) -> _AppliedRepair:
    """Apply a signed unlock adjudication for a sentinel guard file.

    The guard doubles as interrupted-action evidence, so its release is a HUMAN
    adjudication, liveness-checked: a guard whose recorded pid is still alive
    refuses (that is real exclusion, not a wedge). The released guard's bytes
    and the signed record are retained under run evidence — recovery is itself
    a signed fact, never a bare removal.
    """
    if guard_name not in ("resources.guard", "run-transition.guard"):
        raise RepairCeremonyError(f"unknown guard: {guard_name!r}")
    run_dir = Path(runs_root) / run_id
    guard_path = run_dir / guard_name
    if not guard_path.is_file():
        raise RepairCeremonyError(f"guard does not exist: {guard_path}")
    envelope = _verify_adjudication(
        record_path, policy=policy, tessera=tessera, expected_kind="factory-ledger-unlock"
    )
    payload = envelope.payload
    if str(payload.get("run_id", "")) != run_id or str(payload.get("guard", "")) != guard_name:
        raise RepairCeremonyError(
            "adjudication names a different run or guard — exact binding required"
        )
    guard_bytes = guard_path.read_bytes()
    recorded_pid = None
    for token in guard_bytes.decode("utf-8", errors="ignore").split():
        if token.startswith("pid="):
            try:
                recorded_pid = int(token[len("pid=") :])
            except ValueError:
                recorded_pid = None
    if recorded_pid is not None:
        try:
            os.kill(recorded_pid, 0)
        except ProcessLookupError:
            pass  # dead: the wedge case this ceremony exists for
        except PermissionError:
            raise RepairCeremonyError(
                f"guard pid {recorded_pid} appears ALIVE (signal probe denied): "
                f"refusing to unlock a live exclusion"
            ) from None
        else:
            raise RepairCeremonyError(
                f"guard pid {recorded_pid} is ALIVE: a live exclusion is not a "
                f"wedge — refusing to unlock"
            )
    unlock_dir = run_dir / "evidence" / "unlocks"
    unlock_dir.mkdir(parents=True, exist_ok=True)
    sequence = len(list(unlock_dir.glob(f"{guard_name}.*.released"))) + 1
    released = unlock_dir / f"{guard_name}.{sequence}.released"
    record_copy = unlock_dir / f"{guard_name}.{sequence}.tessera.json"
    released.write_bytes(guard_bytes)
    record_copy.write_bytes(Path(record_path).read_bytes())
    guard_path.unlink()
    try:
        fsync_directory_chain(unlock_dir, through=run_dir)
    except DurabilityError as exc:
        raise RepairCeremonyError(str(exc)) from exc
    return _AppliedRepair(
        record_path=record_copy,
        detail=f"released {guard_name} (recorded pid {recorded_pid}) as {released.name}",
    )
