"""Forcing tests for the receipted repair ceremonies (plan 4.2).

Degrade, never wedge — and recovery is one bounded operator action that leaves
a verifiable state plus a persisted signed record, never silence and never a
bare removal. The signature-bearing paths run end-to-end against the real
tessera binary (gated exactly like make test-tessera); the structural refusals
(unreceipted surgery, wrong binding, live pid) need no signer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from factory_runtime.authority import AuthorityPolicy, Principal
from factory_runtime.promotion_gate import PromotionGateError, _load_chain
from factory_runtime.repair_ceremony import (
    RepairCeremonyError,
    apply_ledger_unlock,
    require_quarantine_license,
)
from tests.conftest import promoting_chain_entries


def _tessera_bin() -> str:
    binary = os.environ.get("FACTORY_TESSERA_BIN") or shutil.which("tessera")
    if not binary or not Path(binary).exists():
        pytest.skip("tessera binary not available (set FACTORY_TESSERA_BIN)")
    return binary


def _write_chain(root: Path, lines: list[str]) -> Path:
    receipts = root / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / "chain.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_quarantine_without_its_license_refuses_the_chain(tmp_path: Path) -> None:
    """Unreceipted chain surgery is itself a wedge: a quarantine file with no
    installed signed repair record makes the loader refuse the whole chain."""
    entries = promoting_chain_entries()
    chain = _write_chain(tmp_path, [json.dumps(e, sort_keys=True) for e in entries[:1]])
    (chain.parent / f"quarantine-{entries[1]['hash']}.jsonl").write_text(
        json.dumps(entries[1], sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(PromotionGateError, match="unreceipted chain surgery"):
        _load_chain(chain)
    with pytest.raises(RepairCeremonyError):
        require_quarantine_license(chain.parent)


def test_unlock_refuses_a_live_pid_and_a_wrong_binding(tmp_path: Path) -> None:
    """A live exclusion is not a wedge, and an adjudication that names another
    run or guard binds nothing — both refuse before any verification cost."""

    class _NoVerify:
        def verify_json(self, *_args, **_kwargs):
            class _Env:
                payload = {"run_id": "r1", "guard": "resources.guard"}

            return _Env()

    policy = AuthorityPolicy(
        repository_id="repo",
        policy_id="p",
        root_public_key="c" * 64,
        principals={
            "human:founder": Principal(
                identity="human:founder", kind="human",
                public_key="a" * 64, capabilities=frozenset(),
            )
        },
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest="sha256:" + "0" * 64,
    )
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "resources.guard").write_text(f"pid={os.getpid()}\n", encoding="utf-8")
    record = tmp_path / "record.tessera.json"
    record.write_text("{}", encoding="utf-8")
    with pytest.raises(RepairCeremonyError, match="ALIVE"):
        apply_ledger_unlock(
            tmp_path, "r1", guard_name="resources.guard",
            record_path=record, policy=policy, tessera=_NoVerify(),  # type: ignore[arg-type]
        )

    (run_dir / "run-transition.guard").write_text("pid=999999999\n", encoding="utf-8")
    with pytest.raises(RepairCeremonyError, match="different run or guard"):
        apply_ledger_unlock(
            tmp_path, "r1", guard_name="run-transition.guard",
            record_path=record, policy=policy, tessera=_NoVerify(),  # type: ignore[arg-type]
        )


def _operator(tmp_path: Path) -> tuple[str, Path, str]:
    """A real operator: tessera keypair whose pubkey enrolls as the one human."""
    binary = _tessera_bin()
    key_path = tmp_path / "operator.key"
    keygen = subprocess.run(
        [binary, "keygen", "--output", str(key_path)], capture_output=True, text=True
    )
    if keygen.returncode != 0 or not key_path.exists():
        pytest.skip(f"tessera keygen unavailable: {keygen.stderr.strip()[:120]}")
    from factory_runtime.tessera import TesseraCli

    probe = TesseraCli((binary,)).wrap_json(
        {"probe": True}, kind="factory-test",
        key_path=key_path, output_path=tmp_path / "probe.tessera.json",
    )
    return binary, key_path, probe.public_key


def _policy_for(public_key: str) -> AuthorityPolicy:
    return AuthorityPolicy(
        repository_id="repo",
        policy_id="p",
        root_public_key="c" * 64,
        principals={
            "human:founder": Principal(
                identity="human:founder", kind="human",
                public_key=public_key, capabilities=frozenset(),
            )
        },
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest="sha256:" + "0" * 64,
    )


def test_chain_repair_end_to_end_one_bounded_action(tmp_path: Path) -> None:
    """The R5 exit, property (iii) pinned: ONE operator action (apply the signed
    adjudication) and afterward the chain loads, the quarantine is preserved,
    the signed record is installed, and append-after-repair still grounds."""
    from factory_runtime.repair_ceremony import apply_chain_repair
    from factory_runtime.tessera import TesseraCli

    binary, key_path, public_key = _operator(tmp_path)
    tessera = TesseraCli((binary,))
    policy = _policy_for(public_key)

    import hashlib

    entries = list(promoting_chain_entries())
    # the R5 wedge: a DUPLICATE id appended at the tail (self-consistent, linked).
    dup_body = {k: v for k, v in entries[2].items() if k not in ("hash",)}
    dup_body["id"] = entries[1]["id"]  # duplicate id
    dup_body["prev_hash"] = entries[2]["hash"]
    dup_digest = hashlib.sha256(
        json.dumps(dup_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    wedge = {**dup_body, "hash": dup_digest}
    lines = [json.dumps(e, sort_keys=True) for e in (*entries, wedge)]
    chain = _write_chain(tmp_path / "H", lines)
    with pytest.raises(PromotionGateError, match="duplicate"):
        _load_chain(chain)  # wedged

    adjudication = {
        "offending_entry_hash": wedge["hash"],
        "reason": "duplicate receipt id appended at tail (R5 wedge)",
    }
    record = tessera.wrap_json(
        adjudication, kind="factory-chain-repair",
        key_path=key_path, output_path=tmp_path / "repair.tessera.json",
    )
    applied = apply_chain_repair(
        tmp_path / "H", record_path=record.path, policy=policy, tessera=tessera
    )
    assert applied.record_path.is_file()

    loaded = _load_chain(chain)  # post-repair: loads under its license
    assert set(loaded) == {e["id"] for e in entries}
    quarantine = chain.parent / f"quarantine-{wedge['hash']}.jsonl"
    assert quarantine.is_file()  # nothing destroyed

    # append-after-repair still grounds: chain a fresh entry from the surviving tail
    new_body = {"id": "R-after", "kind": "build", "ts": 9, "prev_hash": entries[-1]["hash"]}
    new_digest = hashlib.sha256(
        json.dumps(new_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with chain.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({**new_body, "hash": new_digest}, sort_keys=True) + "\n")
    assert "R-after" in _load_chain(chain)

    # a second application of the same adjudication refuses — the offense is no
    # longer in the chain (already quarantined), which is the earlier refusal
    with pytest.raises(RepairCeremonyError, match="not in the chain|already"):
        apply_chain_repair(
            tmp_path / "H", record_path=record.path, policy=policy, tessera=tessera
        )


def test_ledger_unlock_end_to_end_retains_the_signed_fact(tmp_path: Path) -> None:
    from factory_runtime.repair_ceremony import apply_ledger_unlock
    from factory_runtime.tessera import TesseraCli

    binary, key_path, public_key = _operator(tmp_path)
    tessera = TesseraCli((binary,))
    policy = _policy_for(public_key)
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "resources.guard").write_text("pid=999999999\n", encoding="utf-8")

    record = tessera.wrap_json(
        {"run_id": "r1", "guard": "resources.guard", "reason": "SIGKILLed action"},
        kind="factory-ledger-unlock",
        key_path=key_path, output_path=tmp_path / "unlock.tessera.json",
    )
    applied = apply_ledger_unlock(
        tmp_path / "runs", "r1", guard_name="resources.guard",
        record_path=record.path, policy=policy, tessera=tessera,
    )
    assert not (run_dir / "resources.guard").exists()  # released
    unlocks = run_dir / "evidence" / "unlocks"
    assert (unlocks / "resources.guard.1.released").read_text().startswith("pid=")
    assert applied.record_path.is_file()  # the signed fact persists


def test_a_non_enrolled_signer_cannot_adjudicate(tmp_path: Path) -> None:
    """The lane-substituted-key refusal: a valid signature from a key OUTSIDE
    the enrolled humans is refused — only the operator-owned roster adjudicates."""
    from factory_runtime.repair_ceremony import apply_chain_repair
    from factory_runtime.tessera import TesseraCli

    binary, key_path, _public_key = _operator(tmp_path)
    tessera = TesseraCli((binary,))
    policy = _policy_for("f" * 64)  # roster enrolls a DIFFERENT key
    entries = promoting_chain_entries()
    _write_chain(tmp_path / "H", [json.dumps(e, sort_keys=True) for e in entries])
    record = tessera.wrap_json(
        {"offending_entry_hash": entries[-1]["hash"], "reason": "x"},
        kind="factory-chain-repair",
        key_path=key_path, output_path=tmp_path / "forged.tessera.json",
    )
    with pytest.raises(RepairCeremonyError, match="adjudication refused"):
        apply_chain_repair(
            tmp_path / "H", record_path=record.path, policy=policy, tessera=tessera
        )
