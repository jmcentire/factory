"""CLI boundary tests for the verdict command.

The command is the orchestrator-facing integration of the verdict/handover core:
typed JSON in, computed verdict and (over a PASS) the composed completion token
out. Malformed inputs are refused controls (exit 2), never tracebacks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest import CaptureFixture

from factory_core.manifest import digest_obj
from factory_runtime.cli import main

CANDIDATE = digest_obj({"artifact": "verdict-cli-candidate"})
VALIDATOR = "validator-seat"


def _signed(body: dict[str, Any]) -> dict[str, Any]:
    return {"body": body, "claimed_digest": digest_obj(body)}


def _coverage_dict() -> dict[str, Any]:
    return {
        "territories": [
            {
                "territory_id": "core-scenario",
                "kind": "scenario",
                "status": "covered",
                "declared_by": "ratification",
                "declaration_position": 10,
            }
        ],
        "adequacy": [],
        "verb_ids": ["do-the-thing"],
        "ratified_position": 1,
    }


def _frame_check_dict(first_line: str = "yes") -> dict[str, Any]:
    body = {
        "first_line": first_line,
        "artifact_digest": CANDIDATE,
        "scenario_instance_digest": digest_obj({"instance": "cli-cold"}),
    }
    return {**body, "evidence": _signed(body)}


def _handover_dict() -> dict[str, Any]:
    body = {
        "token": "__HANDOVER__",
        "handover_id": "coder-1",
        "from_seat": "coder",
        "claim": "delivered",
        "scope": {
            "completed": ["do-the-thing"],
            "explicitly_excluded": [],
            "assumed_in_scope_by_others": [],
        },
        "ledger_position": 100,
        "evidence_digests": [],
        "preconditions_for_next": [],
        "retracts": False,
        "forcing_event_digest": "",
    }
    return {
        "handover_id": "coder-1",
        "from_seat": "coder",
        "claim": "delivered",
        "scope": body["scope"],
        "ledger_position": 100,
        "evidence": _signed(body),
    }


def _write(tmp_path: Path, name: str, payload: Any) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _run_verdict(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    *,
    promotion: dict[str, Any],
    frame_check: dict[str, Any] | None,
    handovers: list[dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]]:
    argv = [
        "verdict",
        "--coverage",
        _write(tmp_path, "coverage.json", _coverage_dict()),
        "--promotion",
        _write(tmp_path, "promotion.json", promotion),
        "--candidate",
        CANDIDATE,
        "--evaluated-position",
        "1000",
        "--validator",
        VALIDATOR,
    ]
    if frame_check is not None:
        argv += ["--frame-check", _write(tmp_path, "frame.json", frame_check)]
    if handovers is not None:
        argv += ["--handovers", _write(tmp_path, "handovers.json", handovers)]
    code = main(argv)
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else {})


def test_pass_verdict_composes_the_reserved_token(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    code, payload = _run_verdict(
        tmp_path,
        capsys,
        promotion={"allowed": True, "disposition": "promote"},
        frame_check=_frame_check_dict("yes"),
        handovers=[_handover_dict()],
    )
    assert code == 0
    assert payload["verdict"]["disposition"] == "pass"
    assert payload["headline"].splitlines()[0] == (
        "Does it do the thing it was built to do? YES"
    )
    assert payload["composition"]["reachable"] is True
    assert payload["composition"]["token"] == "__DONE__"
    assert payload["done_attestation_subject_digest"].startswith("sha256:")


def test_missing_frame_check_is_incomplete_and_token_withheld(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    code, payload = _run_verdict(
        tmp_path,
        capsys,
        promotion={"allowed": True, "disposition": "promote"},
        frame_check=None,
        handovers=[_handover_dict()],
    )
    assert code == 0
    assert payload["verdict"]["disposition"] == "incomplete"
    assert payload["composition"]["reachable"] is False
    assert payload["composition"]["token"] == ""
    assert "done_attestation_subject_digest" not in payload


def test_blocked_promotion_floor_blocks_the_verdict(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    code, payload = _run_verdict(
        tmp_path,
        capsys,
        promotion={"allowed": False, "disposition": "block"},
        frame_check=_frame_check_dict("yes"),
    )
    assert code == 0
    assert payload["verdict"]["disposition"] == "block"
    assert "promotion-not-allowed:block" in payload["verdict"]["reasons"]


def test_malformed_inputs_are_refused_controls(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    coverage = _write(tmp_path, "coverage.json", {"territories": []})
    promotion = _write(tmp_path, "promotion.json", {"allowed": True, "disposition": "promote"})
    code = main(
        [
            "verdict",
            "--coverage",
            coverage,
            "--promotion",
            promotion,
            "--candidate",
            CANDIDATE,
            "--evaluated-position",
            "1000",
            "--validator",
            VALIDATOR,
        ]
    )
    assert code == 2
    assert "refused" in capsys.readouterr().err

    bad_array = _write(tmp_path, "handovers.json", {"not": "an array"})
    code = main(
        [
            "verdict",
            "--coverage",
            _write(tmp_path, "coverage2.json", _coverage_dict()),
            "--promotion",
            promotion,
            "--candidate",
            CANDIDATE,
            "--evaluated-position",
            "1000",
            "--validator",
            VALIDATOR,
            "--handovers",
            bad_array,
        ]
    )
    assert code == 2
