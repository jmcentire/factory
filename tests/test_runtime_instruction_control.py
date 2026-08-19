from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import factory_runtime.cli as runtime_cli
from factory_core.manifest import digest_obj
from factory_runtime.instruction_control import (
    InstructionControlError,
    compile_role_contract,
    derive_effective_directive_contract,
    validate_directive_readback,
    validate_lane_dispatch,
    verify_effective_directive_contract,
    verify_role_contract,
)

NOW = 1_787_144_400


def _hash(body: dict[str, Any]) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _lines(entries: list[dict[str, Any]]) -> bytes:
    return (
        "".join(
            json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
            for entry in entries
        )
    ).encode()


def _signed(
    entries: list[dict[str, Any]],
    *,
    text: str,
    supersedes: str | None = None,
    dispositions: dict[str, Any] | None = None,
    qualifiers: list[str] | None = None,
    verdict: tuple[str, dict[str, str]] | None = None,
    scope: str = "run",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": f"D-{len(entries) + 1:04d}",
        "ts": "2026-08-19T12:00:00+00:00",
        "scope": scope,
        "text": text,
        "qualifiers": qualifiers or [],
        "supersedes": supersedes,
        "dispositions": dispositions,
        "prev_hash": entries[-1]["hash"] if entries else "0" * 64,
    }
    if verdict is not None:
        body[verdict[0]] = verdict[1]
    return {**body, "hash": _hash(body)}


def _provisional(
    entries: list[dict[str, Any]], *, expires: str, scope: str = "run"
) -> dict[str, Any]:
    body = {
        "id": f"P-{len(entries) + 1:04d}",
        "ts": "2026-08-19T12:00:00+00:00",
        "scope": scope,
        "text": "candidate ruling",
        "qualifiers": [],
        "cite": "transcript:1:event:sha256",
        "expires": expires,
        "prev_hash": entries[-1]["hash"] if entries else "0" * 64,
    }
    return {**body, "hash": _hash(body)}


def _contract(ledger: bytes = b"", provisional: bytes = b"") -> dict[str, Any]:
    return derive_effective_directive_contract(
        ledger_bytes=ledger,
        provisional_bytes=provisional,
        run_id="run-1",
        generation=2,
        role="coder",
        evaluated_at=NOW,
    )


def test_effective_contract_is_deterministic_and_exactly_rederived() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="Do not alter migrations", qualifiers=["raise ambiguity"]))
    ledger = _lines(entries)

    first = _contract(ledger)
    second = _contract(ledger)

    assert first == second
    assert first["directives"][0]["directive_id"] == "D-0001"
    assert first["provisional"]["live_unsettled_count"] == 0
    verify_effective_directive_contract(
        first,
        ledger_bytes=ledger,
        provisional_bytes=b"",
        expected_run_id="run-1",
        expected_generation=2,
        expected_role="coder",
    )

    changed: list[dict[str, Any]] = list(entries)
    changed.append(_signed(changed, text="A later directive"))
    with pytest.raises(InstructionControlError) as failure:
        verify_effective_directive_contract(
            first,
            ledger_bytes=_lines(changed),
            provisional_bytes=b"",
            expected_run_id="run-1",
            expected_generation=2,
            expected_role="coder",
        )
    assert failure.value.code == "CONTRACT_MISMATCH"


def test_effective_contract_cannot_be_future_dated() -> None:
    contract = _contract()
    with pytest.raises(InstructionControlError) as future:
        verify_effective_directive_contract(
            contract,
            ledger_bytes=b"",
            provisional_bytes=b"",
            expected_run_id="run-1",
            expected_generation=2,
            expected_role="coder",
            current_time=NOW - 1,
        )
    assert future.value.code == "FUTURE_CONTRACT"


def test_prepare_lane_dispatch_recovers_exact_effective_contract_after_partial_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    provisional = tmp_path / "provisional.jsonl"
    doctrine = tmp_path / "doctrine.md"
    dispatch = tmp_path / "dispatch.json"
    output = tmp_path / "instruction-inputs"
    ledger.write_bytes(b"")
    provisional.write_bytes(b"")
    doctrine.write_text(
        "## Shared foundation\nshared\n\n"
        "## Directive — Validator\nvalidator\n\n"
        "## Directive — Coder\ncoder\n\n"
        "## Directive — Tester\ntester\n",
        encoding="utf-8",
    )
    dispatch.write_text(
        json.dumps(
            {
                "schema_version": "factory-lane-dispatch/1",
                "run_id": "run-1",
                "generation": 2,
                "role": "coder",
                "semantic_clearance": False,
                "interpretation": {
                    "restated_request": "Implement the exact objective.",
                    "operational_consequence": "Return a bounded handoff.",
                    "ambiguity": "none",
                },
                "directive_readback": [],
                "task": "Implement the authorized behavior.",
            }
        ),
        encoding="utf-8",
    )
    arguments = [
        "prepare-lane-dispatch",
        "--dispatch",
        str(dispatch),
        "--directive-ledger",
        str(ledger),
        "--directive-provisional",
        str(provisional),
        "--role-doctrine",
        str(doctrine),
        "--run-id",
        "run-1",
        "--generation",
        "2",
        "--role",
        "coder",
        "--effective-directives-output",
        str(output / "effective.json"),
        "--role-contract-output",
        str(output / "role.json"),
        "--readback-output",
        str(output / "readback.json"),
        "--task-output",
        str(output / "task.txt"),
    ]
    monkeypatch.setattr(runtime_cli.time, "time", lambda: NOW)
    assert runtime_cli.main(arguments) == 0
    effective = (output / "effective.json").read_bytes()

    # Simulate a crash after the first canonical publication and before the remaining files.
    for name in ("role.json", "readback.json", "task.txt"):
        (output / name).unlink()
    monkeypatch.setattr(runtime_cli.time, "time", lambda: NOW + 86_400)
    assert runtime_cli.main(arguments) == 0
    assert (output / "effective.json").read_bytes() == effective
    assert all((output / name).is_file() for name in ("role.json", "readback.json", "task.txt"))


def test_tamper_and_noncanonical_supersession_refuse() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="first"))
    altered = _lines(entries).replace(b"first", b"other")
    with pytest.raises(InstructionControlError, match="altered"):
        _contract(altered)

    entries.append(_signed(entries, text="second", supersedes="D-9999", dispositions={}))
    with pytest.raises(InstructionControlError, match="supersedes no earlier"):
        _contract(_lines(entries))


def test_explicit_supersession_selects_only_successor() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="old", qualifiers=["keep me"]))
    entries.append(
        _signed(
            entries,
            text="new",
            supersedes="D-0001",
            dispositions={"keep me": {"action": "kept", "new": None}},
            qualifiers=["keep me"],
        )
    )
    contract = _contract(_lines(entries))
    assert [item["text"] for item in contract["directives"]] == ["new"]


def test_scope_selection_is_closed_role_run_and_generation_specific() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="global", scope="global"))
    entries.append(_signed(entries, text="coder", scope="role=coder"))
    entries.append(_signed(entries, text="tester", scope="role=tester"))
    entries.append(
        _signed(
            entries,
            text="this invocation",
            scope="run=run-1;generation=2;role=coder",
        )
    )
    entries.append(
        _signed(
            entries,
            text="another run",
            scope="run=run-2;generation=2;role=coder",
        )
    )

    contract = _contract(_lines(entries))

    assert [item["text"] for item in contract["directives"]] == [
        "global",
        "coder",
        "this invocation",
    ]


@pytest.mark.parametrize(
    "scope",
    [
        "orchestrator-only",
        "role=unknown",
        "generation=02",
        "role=coder;run=run-1",
        "run=run-1;unknown=value",
    ],
)
def test_unknown_or_noncanonical_scope_refuses(scope: str) -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="must not become broad", scope=scope))

    with pytest.raises(InstructionControlError) as failure:
        _contract(_lines(entries))

    assert failure.value.code == "INVALID_SCOPE"


def test_scope_change_during_supersession_refuses() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="global", scope="global"))
    entries.append(
        _signed(
            entries,
            text="coder replacement",
            scope="role=coder",
            supersedes="D-0001",
            dispositions={},
        )
    )

    with pytest.raises(InstructionControlError, match="changes scope"):
        _contract(_lines(entries))


def test_inapplicable_provisional_does_not_block_another_role() -> None:
    provisional: list[dict[str, Any]] = []
    provisional.append(
        _provisional(
            provisional,
            expires="2026-12-31T00:00:00+00:00",
            scope="role=tester",
        )
    )

    contract = _contract(provisional=_lines(provisional))

    assert contract["directives"] == []
    assert contract["provisional"]["entry_count"] == 1


def test_live_unsettled_provisional_blocks_instead_of_becoming_instruction() -> None:
    provisional: list[dict[str, Any]] = []
    provisional.append(_provisional(provisional, expires="2026-12-31T00:00:00+00:00"))
    with pytest.raises(InstructionControlError) as failure:
        _contract(provisional=_lines(provisional))
    assert failure.value.code == "UNSETTLED_PROVISIONAL"


def test_refusal_settles_provisional_without_activating_its_text() -> None:
    provisional: list[dict[str, Any]] = []
    provisional.append(_provisional(provisional, expires="2026-12-31T00:00:00+00:00"))
    signed: list[dict[str, Any]] = []
    reference = {
        "id": provisional[0]["id"],
        "hash": provisional[0]["hash"],
        "cite": provisional[0]["cite"],
    }
    signed.append(_signed(signed, text="candidate ruling", verdict=("refuses", reference)))
    assert _contract(_lines(signed), _lines(provisional))["directives"] == []


def test_expired_provisional_is_retained_in_source_identity_but_not_effective() -> None:
    provisional: list[dict[str, Any]] = []
    expired = dt.datetime.fromtimestamp(NOW - 1, tz=dt.UTC).isoformat()
    provisional.append(_provisional(provisional, expires=expired))
    contract = _contract(provisional=_lines(provisional))
    assert contract["directives"] == []
    assert contract["provisional"]["entry_count"] == 1


def test_role_contract_compiles_exact_shared_and_role_sections() -> None:
    doctrine = Path("docs/SOFTWARE-FACTORY.md").read_bytes()
    coder = compile_role_contract(doctrine_bytes=doctrine, role="coder")
    tester = compile_role_contract(doctrine_bytes=doctrine, role="tester")

    assert "## Shared foundation" in coder["instructions"]
    assert "## Directive — Coder" in coder["instructions"]
    assert "## Directive — Tester" not in coder["instructions"]
    assert coder["instructions_digest"] != tester["instructions_digest"]
    verify_role_contract(coder, doctrine_bytes=doctrine, expected_role="coder")


def test_readback_binds_exact_scope_membership_quote_qualifiers_and_ambiguity() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(
        _signed(
            entries,
            text="Do not alter migrations",
            qualifiers=["unless the ratified architecture explicitly requires one"],
        )
    )
    contract = _contract(_lines(entries))
    readback = {
        "schema_version": "factory-directive-readback/1",
        "run_id": "run-1",
        "generation": 2,
        "role": "coder",
        "effective_directive_contract_digest": digest_obj(contract),
        "semantic_clearance": False,
        "task_interpretation": {
            "restated_request": "Do not touch migrations.",
            "operational_consequence": "Ask rather than altering them.",
            "ambiguity": "none",
        },
        "directives": [
            {
                "directive_id": "D-0001",
                "source_quote": "Do not alter migrations",
                "operational_consequence": "Return a specification question instead.",
                "ambiguity": "none",
                "qualifier_readback": [
                    {
                        "source_quote": (
                            "unless the ratified architecture explicitly requires one"
                        ),
                        "operational_consequence": (
                            "Treat only that exact architecture statement as the exception."
                        ),
                        "ambiguity": "none",
                    }
                ],
            }
        ],
    }
    validate_directive_readback(
        readback,
        contract=contract,
        expected_run_id="run-1",
        expected_generation=2,
        expected_role="coder",
    )

    readback["directives"][0]["ambiguity"] = "unresolved"
    with pytest.raises(InstructionControlError) as failure:
        validate_directive_readback(
            readback,
            contract=contract,
            expected_run_id="run-1",
            expected_generation=2,
            expected_role="coder",
        )
    assert failure.value.code == "READBACK_AMBIGUOUS"

    readback["directives"][0]["ambiguity"] = "none"
    readback["directives"][0]["qualifier_readback"] = []
    with pytest.raises(InstructionControlError) as failure:
        validate_directive_readback(
            readback,
            contract=contract,
            expected_run_id="run-1",
            expected_generation=2,
            expected_role="coder",
        )
    assert failure.value.code == "READBACK_QUALIFIER_MISMATCH"

    readback["directives"][0]["qualifier_readback"] = [
        {
            "source_quote": "unless a migration seems useful",
            "operational_consequence": "Treat it as an exception.",
            "ambiguity": "none",
        }
    ]
    with pytest.raises(InstructionControlError) as failure:
        validate_directive_readback(
            readback,
            contract=contract,
            expected_run_id="run-1",
            expected_generation=2,
            expected_role="coder",
        )
    assert failure.value.code == "READBACK_QUALIFIER_MISMATCH"


def test_structured_lane_dispatch_replaces_substring_confirmation() -> None:
    entries: list[dict[str, Any]] = []
    entries.append(_signed(entries, text="Do not alter migrations"))
    contract = _contract(_lines(entries))
    dispatch = {
        "schema_version": "factory-lane-dispatch/1",
        "run_id": "run-1",
        "generation": 2,
        "role": "coder",
        "semantic_clearance": False,
        "interpretation": {
            "restated_request": "Implement without touching migrations.",
            "operational_consequence": "Ask if the task appears to require a migration.",
            "ambiguity": "none",
        },
        "directive_readback": [
            {
                "directive_id": "D-0001",
                "source_quote": "Do not alter migrations",
                "operational_consequence": "Return an explicit question.",
                "ambiguity": "none",
                "qualifier_readback": [],
            }
        ],
        "task": "Implement the authorized behavior.",
    }
    task, readback = validate_lane_dispatch(
        dispatch,
        contract=contract,
        expected_run_id="run-1",
        expected_generation=2,
        expected_role="coder",
    )
    assert task == b"Implement the authorized behavior."
    assert readback["semantic_clearance"] is False

    dispatch["interpretation"]["ambiguity"] = "unresolved"
    with pytest.raises(InstructionControlError) as failure:
        validate_lane_dispatch(
            dispatch,
            contract=contract,
            expected_run_id="run-1",
            expected_generation=2,
            expected_role="coder",
        )
    assert failure.value.code == "READBACK_AMBIGUOUS"
