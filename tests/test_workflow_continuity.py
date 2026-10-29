"""Continuations retain evidence and role boundaries without claiming delivery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.cli import main
from factory_runtime.state_admission import StateAdmissionError, state_dependency_delta
from tests.conftest import promoting_promotion_inputs, write_promoting_chain
from tests.test_runtime_state_admission import _capsule, _dependencies


def test_context_delta_identifies_changes_without_copying_private_contents() -> None:
    before = _capsule()
    sources = _dependencies()
    sources["frozen-task"] = b"Private same-role clarification, not for another author."
    after = _capsule(sources)
    after["run_ledger_head"] = "sha256:" + "9" * 64
    delta = state_dependency_delta(before, after)
    assert set(delta["changed_dependencies"]) == {"frozen-task"}
    assert "role-contract" in delta["unchanged_dependencies"]
    assert delta["changed_checkpoint_fields"] == ["run_ledger_head"]
    assert "Private same-role" not in json.dumps(delta)
    assert state_dependency_delta(before, before)["changed_dependencies"] == {}


@pytest.mark.parametrize("field,value", [
    ("role", "tester"), ("run_id", "another-run"), ("generation", 4),
    ("target_state_digest", "sha256:" + "9" * 64),
])
def test_context_delta_refuses_cross_role_or_subject_reuse(field: str, value: object) -> None:
    before = _capsule()
    after = {**before, field: value}
    with pytest.raises(StateAdmissionError):
        state_dependency_delta(before, after)


def test_context_delta_cli_preserves_role_scope(tmp_path, capsys) -> None:
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    previous.write_text(json.dumps(_capsule()))
    current.write_text(json.dumps(_capsule()))
    args = ["context-delta", "--previous", str(previous), "--current", str(current)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["changed_dependencies"] == {}
    current.write_text(json.dumps({**_capsule(), "role": "tester"}))
    assert main(args) == 2
    assert not capsys.readouterr().out


def _assembly(tmp_path: Path) -> tuple[list[str], dict, Path, Path]:
    run = tmp_path / "runs" / "run-1"
    run.mkdir(parents=True)
    inputs = promoting_promotion_inputs()
    write_promoting_chain(run)
    output = run / "promotion_inputs.json"
    args = [
        "assemble-promotion-inputs", "--runs", str(run.parent), "--run-id", run.name,
        "--candidate-digest", inputs["request"]["candidate_digest"], "--output", str(output),
    ]
    for name, body in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(body))
        args.extend([f"--{name}", str(path)])
    return args, inputs, output, run


def test_assembly_reuses_exact_evidence_and_does_not_close_run(tmp_path, capsys) -> None:
    args, inputs, output, run = _assembly(tmp_path)
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_text()) == inputs
    assert result["inputs_digest"] == digest_obj(inputs)
    assert result["decision"]["allowed"] is True
    assert result["closed"] is False
    assert not (run / "promotion_verdict.json").exists()
    assert not (run / "harness.json").exists()
    retained = output.read_bytes()
    assert main(args) == 0  # an interrupted continuation reuses identical retained evidence
    assert json.loads(capsys.readouterr().out)["reused"] is True
    assert output.read_bytes() == retained
    inputs["request"]["observations"] = []
    (tmp_path / "request.json").write_text(json.dumps(inputs["request"]))
    assert main(args) == 2  # never replace an earlier assembly with different evidence
    assert output.read_bytes() == retained


def test_assembly_reports_missing_evidence_without_inventing_it(tmp_path, capsys) -> None:
    args, inputs, output, _ = _assembly(tmp_path)
    inputs["request"]["observations"] = []
    (tmp_path / "request.json").write_text(json.dumps(inputs["request"]))
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["decision"]["allowed"] is False
    assert json.loads(output.read_text())["request"]["observations"] == []


def test_assembly_refuses_wrong_candidate_or_unverifiable_receipts(tmp_path, capsys) -> None:
    args, _, output, run = _assembly(tmp_path)
    wrong = list(args)
    wrong[wrong.index("--candidate-digest") + 1] = "sha256:" + "0" * 64
    assert main(wrong) == 2
    assert not output.exists()
    assert "candidate differs" in capsys.readouterr().err
    chain = run.parent.parent / "receipts" / "chain.jsonl"
    chain.write_text(chain.read_text().replace(
        '"oracle_adequate": true', '"oracle_adequate": false',
    ))
    assert main(args) == 2
    assert not output.exists()
