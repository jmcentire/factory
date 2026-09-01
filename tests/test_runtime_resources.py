from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.manifest import LedgerIntegrityError, digest_bytes
from factory_runtime.cli import main as cli_main
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.state import RunStore
from tests.conftest import create_intake_run


def _append(
    ledger: ResourceLedger,
    resource_id: str,
    *,
    status: str,
    ownership: str = "run-owned",
    resource_type: str = "source-worktree",
    identifier: str = "/run/target/source",
    baseline: dict[str, object] | None = None,
    disposition: dict[str, object] | None = None,
) -> str:
    return ledger.append(
        generation=1,
        resource_id=resource_id,
        resource_type=resource_type,
        identifier=identifier,
        creator_action="test",
        ownership=ownership,
        baseline=baseline or {"absent_at_plan": True},
        disposition=disposition or {},
        status=status,
        evidence_digests={},
        actor="test",
    )


def test_close_accepts_only_explicit_terminal_dispositions(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    _append(
        ledger,
        "contact",
        status="planned",
        ownership="external-non-owned",
        resource_type="repository-contact",
        identifier="https://example.test/repository.git",
    )
    _append(
        ledger,
        "contact",
        status="succeeded",
        ownership="external-non-owned",
        resource_type="repository-contact",
        identifier="https://example.test/repository.git",
    )
    _append(ledger, "source", status="planned")
    _append(ledger, "source", status="active")

    with pytest.raises(ResourceLedgerError, match="source"):
        ledger.verify_for_close()

    _append(
        ledger,
        "source",
        status="retained",
        disposition={"reason": "evidence retention", "residue": True},
    )
    assert set(ledger.verify_for_close()) == {"contact", "source"}


def test_terminal_seal_is_idempotent_and_refuses_every_later_event(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    _append(ledger, "source", status="active")
    _append(
        ledger,
        "source",
        status="retained",
        disposition={"reason": "retained evidence", "residue": True},
    )

    first = ledger.seal_for_close(actor="validator")
    second = ledger.seal_for_close(actor="validator")

    assert second == first
    assert first["ledger_head"] == ledger.head()
    assert ledger.verify_sealed_for_close()[0] == first
    with pytest.raises(ResourceLedgerError, match="retry actor"):
        ledger.seal_for_close(actor="validator-retry")
    with pytest.raises(ResourceLedgerError, match="sealed"):
        _append(
            ledger,
            "another-resource",
            status="planned",
            identifier="/run/another-resource",
        )


def test_execution_lease_blocks_resource_mutation(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)

    with ledger.run_transition_guard():
        with pytest.raises(ResourceLedgerError, match="run transition guard already exists"):
            _append(ledger, "source", status="planned")

    _append(ledger, "source", status="planned")


def test_tampered_or_symlinked_terminal_seal_is_refused(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    ledger = ResourceLedger(run_dir, "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    _append(ledger, "source", status="active")
    _append(
        ledger,
        "source",
        status="retained",
        disposition={"reason": "retained evidence", "residue": True},
    )
    seal = dict(ledger.seal_for_close(actor="validator"))
    seal["actor"] = "forged"
    ledger.seal_path.write_text(json.dumps(seal), encoding="utf-8")

    with pytest.raises(ResourceLedgerError, match="does not re-derive"):
        ledger.verify_sealed_for_close()

    ledger.seal_path.unlink()
    target = run_dir / "forged-seal.json"
    target.write_text("{}", encoding="utf-8")
    ledger.seal_path.symlink_to(target)
    with pytest.raises(ResourceLedgerError, match="unreadable"):
        ledger.verify_sealed_for_close()


def test_resource_metadata_is_bounded_and_disposition_is_closed_shape(
    tmp_path: Path,
) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    with pytest.raises(ResourceLedgerError, match="exceeds 65536 bytes"):
        _append(
            ledger,
            "oversized",
            status="planned",
            baseline={"payload": "x" * 65_537},
        )
    with pytest.raises(ResourceLedgerError, match="unknown field"):
        _append(
            ledger,
            "unknown-disposition",
            status="planned",
            disposition={"operator_note": "not part of the contract"},
        )
    nested: dict[str, object] = {}
    cursor = nested
    for _ in range(10):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child
    with pytest.raises(ResourceLedgerError, match="nesting depth"):
        _append(ledger, "too-deep", status="planned", baseline=nested)


def test_failed_run_owned_residue_blocks_until_inspected_and_disposed(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    _append(ledger, "source", status="active")
    _append(
        ledger,
        "source",
        status="failed",
        disposition={"reason": "interrupted creation", "residue": True},
    )
    with pytest.raises(ResourceLedgerError, match="source"):
        ledger.verify_for_close()

    _append(
        ledger,
        "source",
        status="disposed",
        disposition={"reason": "inspected and removed", "residue": False},
    )
    assert ledger.verify_for_close()["source"]["status"] == "disposed"


def test_external_non_owned_state_can_never_be_recorded_as_removed(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    _append(
        ledger,
        "contact",
        status="planned",
        ownership="external-non-owned",
        resource_type="repository-contact",
        identifier="https://example.test/repository.git",
    )
    _append(
        ledger,
        "contact",
        status="active",
        ownership="external-non-owned",
        resource_type="repository-contact",
        identifier="https://example.test/repository.git",
    )
    with pytest.raises(ResourceLedgerError, match="external/non-owned"):
        _append(
            ledger,
            "contact",
            status="removed",
            ownership="external-non-owned",
            resource_type="repository-contact",
            identifier="https://example.test/repository.git",
            disposition={"reason": "forbidden", "residue": False},
        )


def test_resource_identity_and_baseline_are_immutable(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    with pytest.raises(ResourceLedgerError, match="identifier"):
        _append(
            ledger,
            "source",
            status="active",
            identifier="/run/other/source",
        )


def test_resource_identifier_refuses_control_characters(tmp_path: Path) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    with pytest.raises(ResourceLedgerError, match="control-free"):
        _append(ledger, "source", status="planned", identifier="/run/source\nforged")


def test_resource_chain_tamper_is_detected(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    ledger = ResourceLedger(run_dir, "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    path = run_dir / "resources.jsonl"
    record = json.loads(path.read_text())
    record["payload"]["resource"]["identifier"] = "/forged"
    path.write_text(json.dumps(record) + "\n")

    with pytest.raises(ResourceLedgerError, match="verification failed"):
        ledger.records()


def test_long_resource_id_uses_a_schema_valid_content_addressed_event_id(
    tmp_path: Path,
) -> None:
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    resource_id = "r" * 128
    _append(ledger, resource_id, status="planned")
    record = ledger.records()[0]
    assert str(record["record_id"]).startswith("event-")
    assert len(str(record["record_id"])) <= 128


def test_crashed_lock_self_repairs_and_cas_mismatch_refuses_append(tmp_path: Path) -> None:
    """4.2 change 7: a lock FILE left by a crashed appender no longer wedges the
    resource ledger — the flock is crash-released, so the append proceeds. The
    compare-and-swap expected-head binding remains the refusal that matters."""
    run_dir = tmp_path / "run-1"
    ledger = ResourceLedger(run_dir, "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    lock = run_dir / "resources.jsonl.lock"
    lock.write_text("interrupted", encoding="utf-8")
    _append(ledger, "source", status="active")  # crash residue: self-repairs

    generic = ledger._ledger()
    from factory_core.manifest import LedgerEntry

    with pytest.raises(LedgerIntegrityError, match="changed after"):
        generic.append(
            LedgerEntry(capability_id="run-1", actor="test", created_at="100"),
            expected_head="sha256:" + ("0" * 64),
        )


def test_cli_disposition_carries_immutable_resource_identity_forward(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    store = RunStore(runs, clock=lambda: 100)
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=digest_bytes(b"target"),
        source_digest=digest_bytes(b"source"),
    )
    ledger = ResourceLedger(runs / "run-1", "run-1", clock=lambda: 100)
    _append(ledger, "workspace", status="planned", identifier="/run/workspace")
    _append(ledger, "workspace", status="active", identifier="/run/workspace")

    result = cli_main(
        [
            "disposition-resource",
            "--runs",
            str(runs),
            "--run-id",
            "run-1",
            "--resource-id",
            "workspace",
            "--status",
            "retained",
            "--reason",
            "preserve evidence",
            "--residue",
            "true",
            "--actor",
            "validator",
        ]
    )

    assert result == 0
    retained = ledger.latest()["workspace"]
    assert retained["generation"] == 1
    assert retained["identifier"] == "/run/workspace"
    assert retained["baseline"] == {"absent_at_plan": True}
    assert retained["disposition"] == {
        "reason": "preserve evidence",
        "residue": True,
    }


def test_keyed_ledger_seals_with_an_hmac_head_and_retries_idempotently(
    tmp_path: Path,
) -> None:
    """Round-8 finding 8-2: a keyed resource ledger's head is an hmac-sha256
    address; the seal must mint, reload, retry idempotently, and verify with
    that vocabulary — the sha256-only seal check bricked every keyed run's
    terminal accounting."""
    from factory_runtime.durability import CHAIN_ROOT_KEY_FILENAME

    (tmp_path / CHAIN_ROOT_KEY_FILENAME).write_bytes(b"founder-root-material\n")
    ledger = ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100)
    _append(ledger, "source", status="planned")
    _append(ledger, "source", status="active")
    _append(
        ledger,
        "source",
        status="retained",
        disposition={"reason": "retained evidence", "residue": True},
    )
    assert ledger.head().startswith("hmac-sha256:")  # vacuous otherwise

    first = ledger.seal_for_close(actor="validator")
    assert first["ledger_head"].startswith("hmac-sha256:")
    second = ledger.seal_for_close(actor="validator")  # the retry _load_seal path
    assert second == first
    assert ledger.verify_sealed_for_close()[0] == first
