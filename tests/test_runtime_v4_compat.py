from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.state import (
    PREVIEW_EVIDENCE_VERIFICATION_KEY,
    RELEASED_V4_RUN_SCHEMA_VERSION,
    RunState,
    RunStateError,
    RunStore,
)
from factory_runtime.transition_obligations import RELEASED_V4_CATALOG_DIGEST
from tests.conftest import fixture_preview_evidence_verifier

_FIXTURE = Path(__file__).parent / "fixtures" / "released_v0_3_run4_preview.json"
_V5_ONLY_EXECUTION_KEYS = {
    "validator-execution-manifest",
    "validator-execution-configuration",
    "validator-execution-environment",
    "validator-execution-snapshot",
}
_V5_ONLY_REVIEW_KEYS = {
    "validator-review-subject",
    "validator-adversarial-review",
    "base-source-snapshot",
    "candidate-change-set",
    "validator-review-authority-context",
    "validator-review-observations-source",
}


def _materialize_released_run(root: Path) -> tuple[str, dict[str, Any]]:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    run_root = root / str(fixture["run_id"])
    archive = base64.b64decode(fixture["archive_base64"], validate=True)
    assert hashlib.sha256(archive).hexdigest() == fixture["archive_sha256"]
    with tarfile.open(fileobj=gzip.GzipFile(fileobj=io.BytesIO(archive)), mode="r:") as source:
        for member in source.getmembers():
            relative = PurePosixPath(member.name)
            assert member.isfile()
            assert not relative.is_absolute() and ".." not in relative.parts
            stream = source.extractfile(member)
            assert stream is not None
            destination = run_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(stream.read())
    return str(fixture["run_id"]), fixture


def _entry(store: RunStore, run_id: str, state: RunState) -> dict[str, Any]:
    return dict(
        next(
            entry
            for entry in store.verified_ledger_entries(run_id)
            if entry.get("to_state") == state
        )
    )


def _rewrite_ledger_from(
    ledger: Path,
    rows: list[dict[str, Any]],
    start: int,
) -> None:
    for index in range(start, len(rows)):
        if index:
            rows[index]["prev_hash"] = rows[index - 1]["entry_hash"]
        body = {key: value for key, value in rows[index].items() if key != "entry_hash"}
        rows[index]["entry_hash"] = digest_obj(body)
    ledger.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def test_released_v0_3_run4_preview_replays_read_only(tmp_path: Path) -> None:
    run_id, fixture = _materialize_released_run(tmp_path)
    ledger = tmp_path / run_id / "ledger.jsonl"
    before = ledger.read_bytes()
    store = RunStore(
        tmp_path,
        preview_evidence_verifier=fixture_preview_evidence_verifier(),
    )

    projection = store.load(run_id)

    assert fixture["source_tag"] == "v0.3.0"
    assert fixture["source_commit"] == "7f5cda86d39a27cef359e326144f62948c2dd221"
    assert fixture["source_tag_object"] == "b4824b162084defcd9d982d2c1911e2d78c8bbe7"
    assert projection.schema_version == RELEASED_V4_RUN_SCHEMA_VERSION
    assert projection.state is RunState.PREVIEW
    assert projection.acceptance_obligation_catalog_digest
    assert ledger.read_bytes() == before

    validating = _entry(store, run_id, RunState.VALIDATING)
    validating_digests = validating["artifact_digests"]
    assert isinstance(validating_digests, dict)
    assert {
        "candidate",
        "acceptance-tests",
        "coder-output-snapshot",
        "tester-output-snapshot",
    } <= set(validating_digests)
    assert not (_V5_ONLY_EXECUTION_KEYS & set(validating_digests))

    preview = _entry(store, run_id, RunState.PREVIEW)
    preview_digests = preview["artifact_digests"]
    preview_payload = preview["payload"]
    assert isinstance(preview_digests, dict)
    assert isinstance(preview_payload, dict)
    assert not (_V5_ONLY_EXECUTION_KEYS & set(preview_digests))
    assert not (_V5_ONLY_REVIEW_KEYS & set(preview_digests))
    assert PREVIEW_EVIDENCE_VERIFICATION_KEY not in preview_payload

    obligation_set_digest = str(preview_digests["transition-obligation-set"])
    obligation_set_path = (
        tmp_path
        / run_id
        / "evidence"
        / "transition-obligations"
        / obligation_set_digest.removeprefix("sha256:")
        / "set.json"
    )
    obligation_set = json.loads(obligation_set_path.read_text(encoding="utf-8"))
    assert obligation_set["catalog_digest"] == RELEASED_V4_CATALOG_DIGEST
    assert [item["obligation_id"] for item in obligation_set["obligations"]] == [
        "ledger-anchor",
        "target-subject",
        "validator-evidence",
        "existing-test-expectations",
    ]

    with pytest.raises(RunStateError, match="legacy run schema cannot advance"):
        store.transition(run_id, RunState.HUMAN_APPROVED, actor="human-approver")
    assert ledger.read_bytes() == before


def test_released_v0_3_preview_requires_live_cryptographic_reverification(
    tmp_path: Path,
) -> None:
    run_id, _ = _materialize_released_run(tmp_path)

    with pytest.raises(RunStateError, match="explicit cryptographic evidence verifier"):
        RunStore(tmp_path).load(run_id)

    envelope = next((tmp_path / run_id).rglob("evidence-bundle.tessera.json"))
    document = json.loads(envelope.read_text(encoding="utf-8"))
    document["fixture_mac"] = "0" * 64
    envelope.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RunStateError, match="signed evidence bundle is invalid"):
        RunStore(
            tmp_path,
            preview_evidence_verifier=fixture_preview_evidence_verifier(),
        ).load(run_id)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("catalog-ratification", "acceptance-obligation-catalog.*receipt"),
        ("catalog-structural", "changes or omits the active acceptance-obligation catalog"),
        ("nonce-count", "requires authority nonce count"),
    ),
)
def test_released_v4_replay_keeps_shared_catalog_and_nonce_controls(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    run_id, _ = _materialize_released_run(tmp_path)
    ledger = tmp_path / run_id / "ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    building_index = next(
        index for index, row in enumerate(rows) if row["to_state"] == RunState.BUILDING
    )
    preview_index = next(
        index for index, row in enumerate(rows) if row["to_state"] == RunState.PREVIEW
    )
    if mutation == "catalog-ratification":
        rows[building_index]["artifact_digests"].pop(
            "acceptance-obligation-catalog:human-receipt"
        )
        changed_index = building_index
    elif mutation == "catalog-structural":
        rows[preview_index]["artifact_digests"]["acceptance_obligation_catalog"] = ""
        changed_index = preview_index
    else:
        rows[preview_index]["payload"]["authority_receipt_nonces"] = ["forged-nonce"]
        changed_index = preview_index
    _rewrite_ledger_from(ledger, rows, changed_index)

    with pytest.raises(RunStateError, match=expected):
        RunStore(
            tmp_path,
            preview_evidence_verifier=fixture_preview_evidence_verifier(),
        ).load(run_id)
