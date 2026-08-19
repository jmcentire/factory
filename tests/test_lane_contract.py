from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from factory_core.manifest import digest_bytes
from factory_runtime.lane_contract import (
    LaneContractError,
    load_lane_completion_receipt,
    load_lane_contract,
    write_lane_contract,
)


def _canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def test_lane_contract_is_canonical_regular_and_private(tmp_path: Path) -> None:
    source = tmp_path / "build-input.json"
    source.write_text("{}", encoding="utf-8")
    work = tmp_path / "work"
    output = tmp_path / "output"
    private = tmp_path / "private"
    work.mkdir()
    output.mkdir()
    private.mkdir()
    contract_path = tmp_path / "lane-contract.json"

    digest = write_lane_contract(
        contract_path,
        contract_id="attempt-01-coder",
        run_id="run-01",
        attempt_id="attempt-01",
        role="coder",
        input_artifacts=(
            {
                "kind": "build-input",
                "path": str(source),
                "digest": digest_bytes(source.read_bytes()),
            },
        ),
        work_directory=work,
        output_directory=output,
        private_directory=private,
        completion_receipt_path=output / "lane-completion.json",
    )

    contract = load_lane_contract(contract_path)
    assert digest == digest_bytes(contract_path.read_bytes())
    assert contract["role"] == "coder"
    assert stat.S_IMODE(contract_path.stat().st_mode) == 0o600


def test_completion_receipt_must_bind_the_issued_contract(tmp_path: Path) -> None:
    receipt_path = tmp_path / "lane-completion.json"
    document = {
        "schema_version": "factory-lane-completion-receipt/1",
        "receipt_id": "receipt-01",
        "run_id": "run-01",
        "attempt_id": "attempt-01",
        "role": "tester",
        "status": "complete",
        "contract_digest": "sha256:" + "a" * 64,
        "declared_artifacts": [],
    }
    receipt_path.write_bytes(_canonical(document))

    assert load_lane_completion_receipt(
        receipt_path,
        expected_run_id="run-01",
        expected_attempt_id="attempt-01",
        expected_role="tester",
        expected_contract_digest="sha256:" + "a" * 64,
    ) == document
    with pytest.raises(LaneContractError, match="does not bind"):
        load_lane_completion_receipt(
            receipt_path,
            expected_run_id="run-01",
            expected_attempt_id="attempt-02",
            expected_role="tester",
            expected_contract_digest="sha256:" + "a" * 64,
        )


def test_contract_loader_rejects_noncanonical_or_symlink_documents(tmp_path: Path) -> None:
    path = tmp_path / "lane-contract.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(LaneContractError, match="not canonical"):
        load_lane_contract(path)

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(LaneContractError, match="not regular"):
        load_lane_contract(path)
