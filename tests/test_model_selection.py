"""The user's model selection is input, not a Factory approval request."""

import json
import os
from pathlib import Path

import pytest

from harness import lane_agent, model_settings


def test_role_defaults_and_explicit_choice_precedence(tmp_path):
    config = tmp_path / "defaults.toml"
    config.write_text('[roles.coder]\nagent="claude"\nmodel="configured"\neffort="high"\n')
    env = {"HOME": str(tmp_path), "FACTORY_CONFIG": str(config)}
    assert model_settings.resolve("coder", environ=env) == {
        "agent": "claude",
        "model": "configured",
        "effort": "high",
    }
    env["FACTORY_CODER_MODEL"] = "user-model"
    assert model_settings.resolve("coder", environ=env)["model"] == "user-model"
    choice = model_settings.resolve("coder", environ=env, explicit={"model": "argument-model"})
    assert choice["model"] == "argument-model"
    assert model_settings.resolve("tester", environ=env) == {
        "agent": "codex",
        "model": "",
        "effort": "",
    }


def test_changed_cli_does_not_inherit_another_cli_model(tmp_path):
    config = tmp_path / "defaults.toml"
    config.write_text('[roles.tester]\nagent="agy"\nmodel="some-gemini"\neffort="high"\n')
    env = {"FACTORY_CONFIG": str(config)}
    assert model_settings.resolve("tester", environ=env, explicit={"agent": "claude"}) == {
        "agent": "claude",
        "model": "",
        "effort": "",
    }
    choice = model_settings.resolve(
        "tester", environ=env, explicit={"agent": "claude", "model": "assigned"}
    )
    assert choice["model"] == "assigned"


@pytest.mark.parametrize(
    "content",
    ['[roles.codre]\nmodel="typo"', "[roles.coder]\nmodel=17", '[roles.coder]\nmodle="typo"'],
)
def test_bad_defaults_do_not_silently_change_selection(tmp_path, content):
    config = tmp_path / "defaults.toml"
    config.write_text(content)
    with pytest.raises(ValueError):
        model_settings.resolve("coder", environ={"FACTORY_CONFIG": str(config)})


def test_missing_explicit_config_errors_but_absent_optional_config_uses_defaults(tmp_path):
    env = {"HOME": str(tmp_path)}
    assert model_settings.resolve("orchestrator", environ=env)["agent"] == "agy"
    with pytest.raises(FileNotFoundError):
        model_settings.resolve("coder", environ={"FACTORY_CONFIG": str(tmp_path / "missing")})


def test_unknown_or_retired_names_are_not_silently_replaced(tmp_path):
    # Retirement is a provider fact: resolving defaults neither probes nor substitutes.
    for model in ("retired-version", "private/unknown", "$(touch not-executed)"):
        choice = model_settings.resolve(
            "coder", environ={"HOME": str(tmp_path)}, explicit={"model": model}
        )
        assert choice["model"] == model
    assert not (tmp_path / "not-executed").exists()


@pytest.mark.parametrize("model", ["private-model-2026", "gpt-future", ""])
def test_selected_models_need_no_family_waiver(tmp_path: Path, model: str) -> None:
    root = tmp_path / "lanes"
    for role in ("coder", "tester"):
        lane_agent.claim_seat(
            root,
            role=role,
            slot=role,
            agent_name="codex",
            model=model,
            codex_home=None,
            gap="",
            pid=os.getpid(),
        )
        lane_agent.confirm_seat(root, role=role, slot=role)
    seats = json.loads((root / "lane-seats.json").read_text())
    assert set(seats) == {"coder", "tester"}
    assert all(
        seat["model"] == model and seat["agent"] == "codex" and seat["status"] == "active"
        for seat in seats.values()
    )


def test_model_freedom_does_not_allow_confirming_another_launch(tmp_path: Path) -> None:
    lane_agent.claim_seat(
        tmp_path,
        role="coder",
        slot="coder",
        agent_name="codex",
        model="private-model",
        codex_home=None,
        gap="",
        pid=os.getpid(),
    )
    with pytest.raises(SystemExit, match="not"):
        lane_agent.confirm_seat(tmp_path, role="coder", slot="different-worker")
    lane_agent.confirm_seat(tmp_path, role="coder", slot="coder")
