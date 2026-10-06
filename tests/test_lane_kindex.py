"""Forcing tests for harness/lane_kindex.py: a lane's Kindex is its own copy of the target's .kin,
under a private HOME with a single profile, and never the operator's graph or another lane's."""

from __future__ import annotations

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lane_kindex", REPO / "harness" / "lane_kindex.py")
assert spec is not None and spec.loader is not None
lane_kindex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane_kindex)


def _kin_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "kin.log"
    kin = tmp_path / "kin"
    kin.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s|%s|%s\\n" "$*" "$HOME" "$KIN_PROFILE" "$KIN_PROJECT_PATH" >> "{log}"\n'
        'if [ "$1" = kinbase ]; then echo \'{"imported": 7, "quarantined": 0}\'; exit 0; fi\n'
        'echo "Import complete: 3 created, 0 updated"\n',
        encoding="utf-8",
    )
    kin.chmod(0o755)
    mcp = tmp_path / "kin-mcp"
    mcp.write_text("#!/bin/sh\n", encoding="utf-8")
    mcp.chmod(0o755)
    monkeypatch.setenv("FACTORY_KIN_BIN", str(kin))
    monkeypatch.setenv("FACTORY_KIN_MCP_BIN", str(mcp))
    return log


def _lane(tmp_path: Path, name: str, *, kin: bool = True) -> Path:
    lane = tmp_path / name
    (lane / ".kin").mkdir(parents=True)
    if kin:
        (lane / ".kin" / "knowledge.jsonl").write_text('{"title":"x"}\n', encoding="utf-8")
    return lane


def test_lane_store_is_built_from_its_own_kin_under_a_private_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _kin_stubs(tmp_path, monkeypatch)
    lane, home = _lane(tmp_path, "coder"), tmp_path / "coder-home"
    record = lane_kindex.prepare(lane, home)

    args, run_home, profile, project = log.read_text().strip().split("|")
    assert args == f"import {lane.resolve()}/.kin/knowledge.jsonl --project-path {lane.resolve()}"
    assert run_home == str(home.resolve()) and profile == "factory-lane"
    assert project == str(lane.resolve())
    assert record["imported_nodes"] == 3 and str(record["source_digest"]).startswith("sha256:")
    assert not (lane / ".kin" / "local").exists()  # the store lives outside the repository

    key, value = str(record["mcp_override"]).split("=", 1)
    server = tomllib.loads(f"x = {value}")["x"]
    assert key == "mcp_servers.kindex" and server["command"].endswith("kin-mcp")
    assert server["env"]["HOME"] == str(home.resolve())
    assert server["env"]["KIN_PROFILE"] == "factory-lane"
    assert server["env"]["KIN_PROJECT_PATH"] == str(lane.resolve())
    assert str(home.resolve() / "store") in (home / ".config/kindex/kin.yaml").read_text()


def test_a_lane_without_kin_still_gets_an_empty_scoped_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _kin_stubs(tmp_path, monkeypatch)
    record = lane_kindex.prepare(_lane(tmp_path, "tester", kin=False), tmp_path / "home")
    assert record["source"] is None and record["imported_nodes"] == 0
    assert not log.exists()
    assert "mcp_servers.kindex=" in str(record["mcp_override"])


def test_a_home_bound_to_another_lane_is_never_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kin_stubs(tmp_path, monkeypatch)
    home = tmp_path / "home"
    lane_kindex.prepare(_lane(tmp_path, "coder"), home)
    lane_kindex.prepare(tmp_path / "coder", home)  # relaunching the same lane keeps its store
    with pytest.raises(SystemExit, match="belongs to another lane"):
        lane_kindex.prepare(_lane(tmp_path, "tester"), home)


def test_missing_kindex_refuses_rather_than_launching_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FACTORY_KIN_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SystemExit, match="need Kindex"):
        lane_kindex.prepare(_lane(tmp_path, "coder"), tmp_path / "home")


def test_cli_prints_one_json_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    _kin_stubs(tmp_path, monkeypatch)
    lane = _lane(tmp_path, "coder")
    assert lane_kindex.main(["prepare", "--repo", str(lane), "--home", str(tmp_path / "h")]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["schema_version"] == "factory-lane-kindex/1"


def test_shared_seed_and_kinbase_evidence_load_and_kinbase_writes_are_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = _kin_stubs(tmp_path, monkeypatch)
    lane = _lane(tmp_path, "coder")
    (lane / ".kin" / "events").mkdir()
    seed = tmp_path / "lane-seed.jsonl"
    seed.write_text('{"title":"shared constraint"}\n', encoding="utf-8")
    record = lane_kindex.prepare(lane, tmp_path / "home", seed)

    calls = [line.split("|")[0] for line in log.read_text().splitlines()]
    assert calls[1] == f"import {seed.resolve()} --project-path {lane.resolve()}"
    assert calls[2] == (f"kinbase sync --repo {lane.resolve()} --project-path {lane.resolve()} "
                        "--mode raw --json")  # verified from the copy, no Kinbase binary
    assert record["seed"]["digest"].startswith("sha256:")  # type: ignore[index]
    assert record["kinbase"] == {"events": str(lane.resolve() / ".kin" / "events"),
                                 "imported": 7, "quarantined": 0, "writes": "unreachable"}
    server = tomllib.loads("x = " + str(record["mcp_override"]).split("=", 1)[1])["x"]
    assert server["env"]["PATH"] == "/usr/bin:/bin"


def test_a_kinbase_binary_on_the_server_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _kin_stubs(tmp_path, monkeypatch)
    bindir = tmp_path / "sys-bin"
    bindir.mkdir()
    (bindir / "kinbase").write_text("#!/bin/sh\n", encoding="utf-8")
    (bindir / "kinbase").chmod(0o755)
    monkeypatch.setattr(lane_kindex, "SERVER_PATH", (str(bindir),))
    with pytest.raises(SystemExit, match="write to Kinbase"):
        lane_kindex.prepare(_lane(tmp_path, "coder"), tmp_path / "home")
