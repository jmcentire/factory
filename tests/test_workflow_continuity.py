"""Continuations retain evidence and role boundaries without claiming delivery."""

from __future__ import annotations

import json

import pytest

from factory_runtime.cli import main
from factory_runtime.state_admission import StateAdmissionError, state_dependency_delta
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
