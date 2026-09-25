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

#: A faithful run, expressed the way a lane actually reports it: the request
#: enumerated with a citation, one artifact attributed to each deliverable, and the
#: lane's own typed judgment on each.
_DELIVERABLES = [
    {
        "deliverable_id": "send-to-channel",
        "directive_ref": "directive:distribution-1",
        "summary": "take our data, adapt it for the channel, push it out",
    }
]
#: The transcript slice the request came from. The human asked for the
#: distribution work, so its deliverable cites a STATED authority.
_UTTERANCES = [
    {
        "utterance_id": "directive:distribution-1",
        "speaker": "human",
        "position": 1,
        "line_digest": "sha256:" + "d" * 64,
    }
]

_ASSESSMENTS = [{"deliverable_id": "send-to-channel", "disposition": "delivered"}]
_ATTRIBUTIONS = [{"artifact_ref": "channel_push.py", "deliverable_id": "send-to-channel"}]

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
    fidelity: bool = True,
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
    if fidelity:
        # The fidelity channel is exercised through argv, not bypassed: a verdict
        # with no enumerated request cannot PASS, so the CLI must carry it.
        argv += [
            "--deliverables",
            _write(tmp_path, "deliverables.json", _DELIVERABLES),
            "--assessments",
            _write(tmp_path, "assessments.json", _ASSESSMENTS),
            "--attributions",
            _write(tmp_path, "attributions.json", _ATTRIBUTIONS),
            "--utterances",
            _write(tmp_path, "utterances.json", _UTTERANCES),
        ]
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
    assert payload["headline"].splitlines()[0] == ("Does it do the thing it was built to do? YES")
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


def test_malformed_inputs_are_refused_controls(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
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


def test_the_cli_refuses_to_pass_a_run_with_no_enumerated_request(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Founder, 2026-09-24: silence is not delivery.

    Everything else green, no fidelity flags: the verdict must not PASS, and it
    must say which channel is missing rather than going quiet.
    """

    code, payload = _run_verdict(
        tmp_path,
        capsys,
        promotion={"allowed": True, "disposition": "promote"},
        frame_check=_frame_check_dict(),
        fidelity=False,
    )
    assert code == 0
    assert payload["verdict"]["disposition"] != "pass"
    assert payload["verdict"]["allowed"] is False
    assert "fidelity-missing" in payload["verdict"]["reasons"]
    assert "fidelity" not in payload


def test_the_cli_blocks_a_run_that_built_something_else(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """The distribution-service failure, driven end to end through argv.

    The request enumerates two deliverables; work is attributed only to the first.
    Two and a half days of adjacent output must not clear the second.
    """

    argv_extra = {
        "deliverables.json": [
            *_DELIVERABLES,
            {
                "deliverable_id": "adapt-for-channel",
                "directive_ref": "directive:distribution-2",
                "summary": "adapt our data to the channel's shape",
            },
        ],
        "assessments.json": [
            *_ASSESSMENTS,
            {"deliverable_id": "adapt-for-channel", "disposition": "delivered"},
        ],
        "attributions.json": [
            {"artifact_ref": "pricing.py", "deliverable_id": "send-to-channel"},
            {"artifact_ref": "availability.py", "deliverable_id": "send-to-channel"},
        ],
    }
    argv = [
        "verdict",
        "--coverage",
        _write(tmp_path, "coverage.json", _coverage_dict()),
        "--promotion",
        _write(tmp_path, "promotion.json", {"allowed": True, "disposition": "promote"}),
        "--candidate",
        CANDIDATE,
        "--evaluated-position",
        "1000",
        "--validator",
        VALIDATOR,
        "--frame-check",
        _write(tmp_path, "frame.json", _frame_check_dict()),
        "--deliverables",
        _write(tmp_path, "deliverables.json", argv_extra["deliverables.json"]),
        "--assessments",
        _write(tmp_path, "assessments.json", argv_extra["assessments.json"]),
        "--attributions",
        _write(tmp_path, "attributions.json", argv_extra["attributions.json"]),
        "--utterances",
        _write(
            tmp_path,
            "utterances.json",
            [
                *_UTTERANCES,
                {
                    "utterance_id": "directive:distribution-2",
                    "speaker": "human",
                    "position": 2,
                    "line_digest": "sha256:" + "e" * 64,
                },
            ],
        ),
    ]
    code = main(argv)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"]["disposition"] == "block"
    assert "asked-for-and-not-built:adapt-for-channel" in payload["verdict"]["reasons"]
    assert payload["fidelity"]["unserved"] == ["adapt-for-channel"]
    assert payload["fidelity"]["risk_acceptance_available"] is False


def test_an_enumerated_request_must_say_where_it_came_from(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """A citation the factory cannot check is a claim, not authority.

    Founder, 2026-09-24: "You know who said what." So the transcript slice is
    mandatory whenever a request is enumerated — otherwise directive_ref is a
    string the caller asserts.
    """

    argv = [
        "verdict",
        "--coverage",
        _write(tmp_path, "coverage.json", _coverage_dict()),
        "--promotion",
        _write(tmp_path, "promotion.json", {"allowed": True, "disposition": "promote"}),
        "--candidate",
        CANDIDATE,
        "--evaluated-position",
        "1000",
        "--validator",
        VALIDATOR,
        "--frame-check",
        _write(tmp_path, "frame.json", _frame_check_dict()),
        "--deliverables",
        _write(tmp_path, "deliverables.json", _DELIVERABLES),
        "--assessments",
        _write(tmp_path, "assessments.json", _ASSESSMENTS),
        "--attributions",
        _write(tmp_path, "attributions.json", _ATTRIBUTIONS),
    ]
    code = main(argv)
    assert code == 2  # a refused control, never a soft pass
    assert "requires --utterances" in capsys.readouterr().err


def test_a_deliverable_the_lane_invented_for_itself_blocks(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """The 2.5-day failure at its root.

    The lane's own unsurfaced decision is dressed as a request. Disclosure and
    unilateral decisions cannot stand behind a deliverable: being told, or never
    being told, is not asking for something.
    """

    invented = [
        {
            "deliverable_id": "rework-pricing",
            "directive_ref": "lane:my-own-idea",
            "summary": "pricing work nobody asked for",
        }
    ]
    argv = [
        "verdict",
        "--coverage",
        _write(tmp_path, "coverage.json", _coverage_dict()),
        "--promotion",
        _write(tmp_path, "promotion.json", {"allowed": True, "disposition": "promote"}),
        "--candidate",
        CANDIDATE,
        "--evaluated-position",
        "1000",
        "--validator",
        VALIDATOR,
        "--frame-check",
        _write(tmp_path, "frame.json", _frame_check_dict()),
        "--deliverables",
        _write(tmp_path, "deliverables.json", invented),
        "--assessments",
        _write(
            tmp_path,
            "assessments.json",
            [{"deliverable_id": "rework-pricing", "disposition": "delivered"}],
        ),
        "--attributions",
        _write(
            tmp_path,
            "attributions.json",
            [{"artifact_ref": "pricing.py", "deliverable_id": "rework-pricing"}],
        ),
        "--utterances",
        _write(
            tmp_path,
            "utterances.json",
            [
                {
                    "utterance_id": "lane:my-own-idea",
                    "speaker": "agent",
                    "position": 1,
                    "line_digest": "sha256:" + "f" * 64,
                    "surfaced": False,
                }
            ],
        ),
    ]
    code = main(argv)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"]["disposition"] == "block"
    assert "no-human-authority-asked-for:rework-pricing" in payload["verdict"]["reasons"]
    assert payload["fidelity"]["uncited"] == ["rework-pricing"]
    # The receipts say plainly whose idea it was.
    rows = payload["directive_receipts"]
    assert rows[0]["tier"] == "unilateral"
    assert rows[0]["speaker"] == "agent"
    assert payload["fidelity"]["stated_refs"] == []


def test_the_audit_subcommand_still_speaks_its_vocabulary(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """The gap that let 1501 green tests hide a silenced monitor.

    No test invoked the `audit` subcommand at all — tests/test_audit.py builds
    Verdict objects by hand. So when the fidelity channel started capping an
    un-supplied verdict at INCOMPLETE, audit's entire vocabulary went empty
    through the CLI and nothing noticed. This drives argv end to end and asserts
    the audit still emits a code, which is the only thing that would have caught it.
    """

    argv = [
        "audit",
        "--run-id",
        "run-1",
        "--coverage",
        _write(tmp_path, "coverage.json", _coverage_dict()),
        "--promotion",
        # audit_run ranks the promotion disposition in the VERDICT vocabulary.
        _write(tmp_path, "promotion.json", {"allowed": True, "disposition": "pass"}),
        "--candidate",
        CANDIDATE,
        "--evaluated-position",
        "1000",
        "--validator",
        VALIDATOR,
        "--frame-check",
        _write(tmp_path, "frame.json", _frame_check_dict()),
        "--deliverables",
        _write(tmp_path, "deliverables.json", _DELIVERABLES),
        "--assessments",
        _write(tmp_path, "assessments.json", _ASSESSMENTS),
        "--attributions",
        _write(tmp_path, "attributions.json", _ATTRIBUTIONS),
        "--utterances",
        _write(tmp_path, "utterances.json", _UTTERANCES),
    ]
    code = main(argv)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    # A fully green run is a PASS, so the audit must classify it rather than
    # returning an empty set because the verdict silently fell to INCOMPLETE.
    # Before the fix this was {"codes": [], "rows": []} on this exact input.
    assert payload["codes"], "the audit vocabulary went silent"
    assert "uncoded-pass" in payload["codes"]
    assert payload["rows"] and payload["rows"][0]["run_id"] == "run-1"
