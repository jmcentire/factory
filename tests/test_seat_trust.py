"""A seat's own folder, and only its own Codex report hooks, are trusted before it starts."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "harness"))

import seat_trust  # noqa: E402


def test_claude_trusts_only_the_seat_folder_and_keeps_the_rest(tmp_path: pathlib.Path) -> None:
    profile = tmp_path / ".claude.json"
    profile.write_text(json.dumps({"oauthAccount": {"x": 1},
                                   "projects": {"/elsewhere": {"allowedTools": ["Bash"]}}}))
    profile.chmod(0o600)
    lane = tmp_path / "lane"
    lane.mkdir()
    assert seat_trust.claude(lane, {"HOME": str(tmp_path)}) is True
    data = json.loads(profile.read_text())
    assert data["projects"][str(lane.resolve())] == {"hasTrustDialogAccepted": True}
    assert data["projects"]["/elsewhere"] == {"allowedTools": ["Bash"]}
    assert data["oauthAccount"] == {"x": 1}
    assert profile.stat().st_mode & 0o777 == 0o600
    assert seat_trust.claude(lane, {"HOME": str(tmp_path)}) is False


def test_claude_profile_dir_is_used_and_absent_or_linked_profiles_are_left(
        tmp_path: pathlib.Path) -> None:
    lane = tmp_path / "lane"
    lane.mkdir()
    assert seat_trust.claude(lane, {"HOME": str(tmp_path)}) is False
    assert not (tmp_path / ".claude.json").exists()
    real = tmp_path / "real.json"
    real.write_text("{}")
    (tmp_path / ".claude.json").symlink_to(real)
    assert seat_trust.claude(lane, {"HOME": str(tmp_path)}) is False
    assert real.read_text() == "{}"
    chosen = tmp_path / "profile"
    chosen.mkdir()
    (chosen / ".claude.json").write_text("{}")
    assert seat_trust.claude(lane, {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(chosen)})
    assert json.loads((chosen / ".claude.json").read_text())["projects"]


def test_codex_trusts_the_folder_and_only_its_own_project_hooks(tmp_path: pathlib.Path) -> None:
    lane = tmp_path / "lane"
    (lane / ".codex").mkdir(parents=True)
    own = str(lane.resolve() / ".codex/hooks.json")
    writes = tmp_path / "writes.jsonl"
    fake = tmp_path / "codex"
    fake.write_text("#!" + sys.executable + "\n" + textwrap.dedent(f"""
        import json, sys
        own, writes = {own!r}, {str(writes)!r}
        hooks = [
            {{"key": own + ":stop:0:0", "source": "project", "sourcePath": own,
              "trustStatus": "untrusted", "currentHash": "sha256:" + "a" * 64}},
            {{"key": "/home/.codex/hooks.json:pre_tool_use:0:0", "source": "user",
              "sourcePath": "/home/.codex/hooks.json", "trustStatus": "untrusted",
              "currentHash": "sha256:" + "b" * 64}},
            {{"key": "/other/.codex/hooks.json:stop:0:0", "source": "project",
              "sourcePath": "/other/.codex/hooks.json", "trustStatus": "untrusted",
              "currentHash": "sha256:" + "c" * 64}},
        ]
        assert sys.argv[1:] == ["app-server", "--stdio"]
        for line in sys.stdin:
            request = json.loads(line)
            if "id" not in request:
                continue
            if request["method"] == "config/value/write":
                open(writes, "a").write(json.dumps(request["params"]) + "\\n")
            listed = {{"data": [{{"cwd": ".", "hooks": hooks}}]}}
            result = listed if request["method"] == "hooks/list" else {{"status": "ok"}}
            print(json.dumps({{"id": request["id"], "result": result}}), flush=True)
        """))
    fake.chmod(0o755)
    trusted = seat_trust.codex(str(fake), lane, dict(os.environ))
    assert trusted == [own + ":stop:0:0"]
    folder, hook = [json.loads(line) for line in writes.read_text().splitlines()]
    assert folder == {"keyPath": "projects", "mergeStrategy": "upsert",
                      "value": {str(lane.resolve()): {"trust_level": "trusted"}}}
    assert hook["value"] == {"state": {own + ":stop:0:0": {"trusted_hash": "sha256:" + "a" * 64}}}


def test_off_unless_asked_for() -> None:
    assert not seat_trust.enabled({}) and not seat_trust.enabled({"FACTORY_SEAT_TRUST": "0"})
    assert seat_trust.enabled({"FACTORY_SEAT_TRUST": "1"})


def _fake_codex(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    writes = tmp_path / "writes.jsonl"
    fake = tmp_path / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text("#!" + sys.executable + "\n" + textwrap.dedent(f"""
        import json, sys
        if sys.argv[1:] != ["app-server", "--stdio"]:
            sys.exit(0)  # the coordinator's own session: nothing to do here
        for line in sys.stdin:
            request = json.loads(line)
            if "id" not in request:
                continue
            if request["method"] == "config/value/write":
                open({str(writes)!r}, "a").write(json.dumps(request["params"]) + "\\n")
            listed = {{"data": [{{"cwd": ".", "hooks": []}}]}}
            result = listed if request["method"] == "hooks/list" else {{"status": "ok"}}
            print(json.dumps({{"id": request["id"], "result": result}}), flush=True)
        """))
    fake.chmod(0o755)
    return fake, writes


def test_terminal_codex_coordinator_trusts_its_repository_only_when_asked(
        tmp_path: pathlib.Path, monkeypatch) -> None:
    # Seen in a run: a Codex Validator (a terminal coordinator seat) stopped at Codex's
    # "Trust this folder?" although FACTORY_SEAT_TRUST=1 was set.
    from harness import lane_agent

    fake, writes = _fake_codex(tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    for name in ("HOME", "CODEX_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    monkeypatch.chdir(repository)
    monkeypatch.delenv("FACTORY_TMUX_SOCKET", raising=False)

    def start(switch: str | None) -> None:
        if switch is None:
            monkeypatch.delenv("FACTORY_SEAT_TRUST", raising=False)
        else:
            monkeypatch.setenv("FACTORY_SEAT_TRUST", switch)
        assert lane_agent.main(["run", "--slot", "validator", "--terminal",
                                "--log", str(tmp_path / "lane.log"),
                                "--exits", str(tmp_path / "exits.jsonl"),
                                "--", str(fake)]) == 0

    start(None)
    start("0")
    assert not writes.exists()
    start("1")
    folder = json.loads(writes.read_text().splitlines()[0])
    assert folder["value"] == {str(repository.resolve()): {"trust_level": "trusted"}}
