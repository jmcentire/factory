"""Forcing tests for factory_runtime/model_profiles.py and `factory profile`: a profile binds every
role to an agent the harness can launch and a model the operator named, nothing fills a gap, and
the run's choice of profile is explicit, ordered and refused when it cannot be honoured."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from factory_runtime import cli
from factory_runtime import model_profiles as mp
from harness.model_profile import main as harness_main

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "harness"

BINDINGS = {
    "validator": "claude:opus-test",
    "orchestrator": "agy:gemini-test",
    "coder": "codex-ollama:glm-test:cloud",
    "tester": "codex:gpt-test",
}


def _profile(name: str = "local", **overrides: str) -> mp.Profile:
    bindings = {**BINDINGS, **overrides}
    return mp.Profile(name, "", {role: mp.parse_binding(role, bindings[role]) for role in mp.ROLES})


def _document(**profile_overrides: object) -> dict[str, object]:
    profile = _profile().document()
    profile.update(profile_overrides)
    return {"schema_version": mp.SCHEMA_VERSION, "default": "local", "profiles": {"local": profile}}


def _load_document(tmp_path: Path, document: dict[str, object]) -> mp.Profiles:
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return mp.load(path)


def _case_agents(script: str, variable: str) -> set[str]:
    """The agents a harness script's `case "$VARIABLE" in a|b) ;;` admits."""

    text = (HARNESS / script).read_text(encoding="utf-8")
    match = re.search(rf'case "\${variable}" in\s+([a-z|-]+)\) ;;', text)
    assert match is not None, f"{script} has no case over ${variable}"
    return set(match.group(1).split("|"))


def test_launchable_agents_are_exactly_what_the_harness_launches() -> None:
    """A profile may name only a seat the scripts can start, and every seat they can start."""

    assert set(mp.LAUNCHABLE["validator"]) == _case_agents("factory.sh", "VALIDATOR_AGENT")
    assert set(mp.LAUNCHABLE["orchestrator"]) == _case_agents("factory.sh", "ORCHESTRATOR_AGENT")
    for lane in ("coder", "tester"):
        assert set(mp.LAUNCHABLE[lane]) == _case_agents("tmux_lane.sh", "AGENT")


def test_model_charset_is_the_harness_guard() -> None:
    """The lane splices the model into an argument list; the profile admits only the charset
    model_availability.sh admits, so a model is always one shell word."""

    guard = (HARNESS / "model_availability.sh").read_text(encoding="utf-8")
    assert "*[!A-Za-z0-9._:/-]*" in guard
    assert mp.MODEL_RE.pattern == r"^[A-Za-z0-9._:/-]{1,128}$"
    with pytest.raises(mp.ProfileError):
        mp.parse_binding("tester", "codex:gpt test")
    with pytest.raises(mp.ProfileError):
        mp.parse_binding("tester", "codex:$(touch x)")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"roles": {k: v for k, v in _profile().document()["roles"].items() if k != "tester"}},
         "must bind every role"),
        ({"roles": {**_profile().document()["roles"], "coder": {"agent": "codex"}}},
         "exactly 'agent' and 'model'"),
        ({"roles": {**_profile().document()["roles"], "coder": {"agent": "codex", "model": ""}}},
         "must be named"),
        ({"roles": {**_profile().document()["roles"], "validator": {"agent": "agy",
                                                                     "model": "m"}}},
         "cannot be launched for this role"),
        ({"extra": 1}, "only 'roles'"),
    ],
)
def test_a_partial_or_unlaunchable_profile_is_refused_never_completed(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    with pytest.raises(mp.ProfileError, match=message):
        _load_document(tmp_path, _document(**change))


def test_default_must_name_a_profile_and_names_are_constrained(tmp_path: Path) -> None:
    document = _document()
    document["default"] = "missing"
    with pytest.raises(mp.ProfileError, match="names no profile"):
        _load_document(tmp_path, document)
    with pytest.raises(mp.ProfileError, match="profile name"):
        mp.validate_name("Has Space")


def test_resolution_order_is_flag_then_environment_then_default(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    assert mp.resolve(path, "", {}) is None  # no profiles, nothing asked: earlier behaviour
    with pytest.raises(mp.ProfileError, match="no model profiles"):
        mp.resolve(path, "local", {})
    mp.save(path, mp.Profiles("local", {"local": _profile(), "hosted": _profile("hosted")}))
    assert mp.resolve(path, "", {}).name == "local"
    assert mp.resolve(path, "", {"FACTORY_PROFILE": "hosted"}).name == "hosted"
    assert mp.resolve(path, "local", {"FACTORY_PROFILE": "hosted"}).name == "local"
    with pytest.raises(mp.ProfileError, match="no model profile named 'nope'"):
        mp.resolve(path, "nope", {})


def test_default_path_honours_the_override_and_xdg() -> None:
    assert mp.default_path({"FACTORY_PROFILES": "/x/p.json"}) == Path("/x/p.json")
    assert mp.default_path({"XDG_CONFIG_HOME": "/cfg", "HOME": "/h"}) == Path(
        "/cfg/factory/profiles.json"
    )
    assert mp.default_path({"HOME": "/h"}) == Path("/h/.config/factory/profiles.json")


def test_save_is_owner_only_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "profiles.json"
    profiles = mp.Profiles("local", {"local": _profile()})
    mp.save(path, profiles)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert mp.load(path) == profiles


def test_snapshot_records_how_each_model_reaches_its_agent(tmp_path: Path) -> None:
    """agy takes no model flag, so its model is recorded as set inside the agent."""

    profile = _profile()
    run_root = tmp_path / "run"
    run_root.mkdir()
    target = run_root / "model-profile.json"
    mp.write_snapshot(profile, tmp_path / "profiles.json", target)
    recorded = json.loads(target.read_text(encoding="utf-8"))
    assert recorded["digest"] == profile.digest
    assert recorded["roles"]["orchestrator"]["model_passing"] == "set-in-agent"
    assert recorded["roles"]["tester"]["model_passing"] == "command-line"
    assert mp.read_snapshot(target) == profile.roles
    link = run_root / "linked.json"
    link.symlink_to(target)
    with pytest.raises(mp.ProfileError, match="not a regular file"):
        mp.read_snapshot(link)
    with pytest.raises(mp.ProfileError, match="symlinked"):
        mp.write_snapshot(profile, tmp_path / "profiles.json", link)


def test_harness_entry_prints_nul_fields_and_refuses_a_changed_profile(
    tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    path = tmp_path / "profiles.json"
    assert harness_main(["resolve", "--profiles", str(path)]) == 0
    assert capsysbinary.readouterr().out == b"none\0"
    profile = _profile()
    mp.save(path, mp.Profiles("local", {"local": profile}))
    assert harness_main(["resolve", "--profiles", str(path)]) == 0
    fields = capsysbinary.readouterr().out.split(b"\0")[:-1]
    assert fields[:3] == [b"profile", b"local", profile.digest.encode()]
    assert fields[3:] == [
        part.encode() for role in mp.ROLES for part in BINDINGS[role].split(":", 1)
    ]
    output = tmp_path / "model-profile.json"
    assert harness_main(["snapshot", "--profiles", str(path), "--name", "local",
                    "--expect-digest", "sha256:" + "0" * 64, "--output", str(output)]) == 64
    assert not output.exists()
    assert harness_main(["snapshot", "--profiles", str(path), "--name", "local",
                    "--expect-digest", profile.digest, "--output", str(output)]) == 0
    capsysbinary.readouterr()
    assert harness_main(["binding", "--snapshot", str(output), "--role", "coder"]) == 0
    assert capsysbinary.readouterr().out == b"codex-ollama\0glm-test:cloud\0"
    assert harness_main(["resolve", "--profiles", str(path), "--name", "nope"]) == 64
    assert capsysbinary.readouterr().out == b""  # a refusal prints no first field


def test_prompt_asks_again_until_every_binding_is_launchable() -> None:
    answers = iter(["Bad Name", "local", "codex", "gpt-val", "codex", "gpt-orch",
                    "codex", "has space", "codex", "gpt-coder", "codex", "gpt-tester"])
    said: list[str] = []
    profile = mp.prompt_profile(lambda _: next(answers), said.append)
    assert profile.name == "local"
    assert profile.roles["coder"] == mp.Binding("codex", "gpt-coder")
    assert sum("profile name" in text or "must be named" in text for text in said) == 2


def _factory(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = cli.main(["profile", *args])
    out = capsys.readouterr().out
    return code, (json.loads(out) if code == 0 and out.strip() else {})


def test_cli_creates_lists_shows_switches_and_deletes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "profiles.json")
    flags = [f"--{role}={binding}" for role, binding in BINDINGS.items()]
    code, saved = _factory(["create", "local", "--profiles", path, *flags], capsys)
    assert code == 0 and saved["default"] == "local"  # the first profile is the default
    code, saved = _factory(["create", "hosted", "--profiles", path, *flags[:-1],
                            "--tester=codex:gpt-hosted"], capsys)
    assert code == 0 and saved["default"] == "local"  # a later one only with --default
    assert cli.main(["profile", "create", "local", "--profiles", path, *flags]) == 2
    assert "pass --replace" in capsys.readouterr().err
    code, listed = _factory(["list", "--profiles", path], capsys)
    assert sorted(listed["profiles"]) == ["hosted", "local"]
    code, shown = _factory(["show", "hosted", "--profiles", path], capsys)
    assert shown["roles"]["tester"] == {"agent": "codex", "model": "gpt-hosted"}
    assert shown["default"] is False
    assert _factory(["delete", "local", "--profiles", path], capsys)[0] == 2  # it is the default
    assert _factory(["use", "hosted", "--profiles", path], capsys)[1]["default"] == "hosted"
    assert _factory(["delete", "local", "--profiles", path], capsys)[0] == 0
    assert list(mp.load(Path(path)).profiles) == ["hosted"]


def test_cli_refuses_a_half_named_profile_without_a_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "profiles.json"
    code = cli.main(["profile", "create", "local", "--profiles", str(path),
                     "--validator=claude:opus-test"])
    assert code == 2
    assert "missing: --orchestrator, --coder, --tester" in capsys.readouterr().err
    assert not path.exists()
