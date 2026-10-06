"""Forcing tests for harness/lane_kindex.py: each lane gets `kindex-lite` bound to its own copy,
loaded from approved inputs only, and the launch is refused (never downgraded) unless the lane's
own server proves it can read Kinbase and cannot write to it."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STUB = REPO / "tests" / "fixtures" / "kindex_lite_stub.py"
spec = importlib.util.spec_from_file_location("lane_kindex", REPO / "harness" / "lane_kindex.py")
assert spec is not None and spec.loader is not None
lane_kindex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane_kindex)

GOOD_TOOLS = ["add", "kinbase_explain", "kinbase_sync", "list_nodes", "scope_info", "search"]


def install_kindex(bindir: Path, *, version: str = "0.48.1", tools: list[str] | None = None,
                   scope: dict[str, object] | None = None) -> Path:
    """A fake Kindex install: `kindex-lite` (the MCP stub) and `kin` side by side."""

    bindir.mkdir(parents=True, exist_ok=True)
    lite = bindir / "kindex-lite"
    lite.write_text(f"#!{sys.executable}\n" + STUB.read_text(encoding="utf-8"), encoding="utf-8")
    lite.chmod(0o755)
    (bindir / "stub-config.json").write_text(
        json.dumps({"tools": tools or GOOD_TOOLS, "scope": scope or {}}), encoding="utf-8"
    )
    log = bindir / "kin.log"
    kin = bindir / "kin"
    kin.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = --version ]; then echo "kin {version} (Kindex)"; exit 0; fi\n'
        f'printf "%s|%s|%s\\n" "$*" "$HOME" "$KIN_PROJECT_PATH" >> "{log}"\n'
        'mkdir -p "$KIN_PROJECT_PATH/.kin/local/kindex"\n'
        'if [ "$1" = kinbase ]; then echo \'{"imported": 7, "quarantined": 0}\'; exit 0; fi\n'
        'echo "Import complete: 3 created, 0 updated"\n',
        encoding="utf-8",
    )
    kin.chmod(0o755)
    return lite


@pytest.fixture
def kindex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    def configure(**options: object) -> Path:
        lite = install_kindex(tmp_path / "kindex-bin", **options)  # type: ignore[arg-type]
        monkeypatch.setenv("FACTORY_KINDEX_LITE_BIN", str(lite))
        return lite.parent / "kin.log"
    return configure


def _lane(tmp_path: Path, name: str, *, kin: bool = True) -> Path:
    lane = tmp_path / name
    (lane / ".kin").mkdir(parents=True)
    if kin:
        (lane / ".kin" / "knowledge.jsonl").write_text('{"title":"x"}\n', encoding="utf-8")
    return lane


def _calls(log: Path) -> list[str]:
    return [line.split("|")[0] for line in log.read_text().splitlines()] if log.exists() else []


def test_fresh_lane_loads_approved_inputs_and_binds_kindex_lite_to_its_copy(
    tmp_path: Path, kindex: Callable[..., Path]
) -> None:
    log = kindex()
    lane, home = _lane(tmp_path, "coder"), tmp_path / "coder-home"
    (lane / ".kin" / "events").mkdir()
    seed = tmp_path / "lane-seed.jsonl"
    seed.write_text('{"title":"shared constraint","tags":"run-7"}\n', encoding="utf-8")
    record = lane_kindex.prepare(lane, home, seed)
    lane, home = lane.resolve(), home.resolve()

    tagged = home / "lane-seed.tagged.jsonl"
    assert _calls(log) == [
        f"import {lane}/.kin/knowledge.jsonl --project-path {lane}",
        f"import {tagged} --project-path {lane}",
        f"kinbase sync --repo {lane} --project-path {lane} --mode raw --json",
    ]
    assert all(line.endswith(f"|{home}|{lane}") for line in log.read_text().splitlines())
    assert json.loads(tagged.read_text())["domains"] == ["run-7", "lane-seed"]
    assert record["server"] == "kindex-lite" and record["kindex_version"] == "0.48.1"
    assert record["store"] == f"{lane}/.kin/local/kindex"
    assert record["kinbase"] == {"events": f"{lane}/.kin/events", "imported": 7, "quarantined": 0,
                                 "reads": "frozen-at-launch", "writes": "unavailable"}
    assert record["seed"]["digest"].startswith("sha256:")  # type: ignore[index]
    assert record["kinbase_submissions_allowed"] is False and record["relaunch"] is False

    server = tomllib.loads("x = " + str(record["mcp_override"]).split("=", 1)[1])["x"]
    assert Path(server["command"]).name == "kindex-lite"
    assert server["args"] == ["--repo", str(lane)]
    assert server["env"] == {"HOME": str(home), "KIN_PROJECT_PATH": str(lane),
                             "PATH": "/usr/bin:/bin"}


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"tools": [*GOOD_TOOLS, "kinbase_submit"]}, "write tools exposed"),
        ({"tools": [*GOOD_TOOLS, "kinbase_status"]}, "write tools exposed"),
        ({"tools": [t for t in GOOD_TOOLS if t != "kinbase_sync"]}, "missing"),
        ({"scope": {"kinbase_submissions_allowed": True}}, "scope_info"),
        ({"scope": {"repo": "/elsewhere"}}, "scope_info"),
        ({"scope": {"data_dir": "/Users/someone/.kindex"}}, "scope_info"),
        ({"version": "0.47.9"}, "predates"),
    ],
)
def test_the_launch_is_refused_and_cleaned_up_unless_the_server_proves_read_only(
    tmp_path: Path, kindex: Callable[..., Path], options: dict[str, object], reason: str
) -> None:
    kindex(**options)
    lane, home = _lane(tmp_path, "coder"), tmp_path / "coder-home"
    with pytest.raises(SystemExit, match=reason):
        lane_kindex.prepare(lane, home)
    assert not home.exists() and not (lane / ".kin" / "local").exists()


def test_relaunch_keeps_the_lanes_store_and_another_lane_never_shares_it(
    tmp_path: Path, kindex: Callable[..., Path]
) -> None:
    log = kindex()
    lane, home = _lane(tmp_path, "coder"), tmp_path / "home"
    lane_kindex.prepare(lane, home)
    before = _calls(log)
    again = lane_kindex.prepare(lane, home)
    assert again["relaunch"] is True and _calls(log) == before  # nothing reloaded
    with pytest.raises(SystemExit, match="belongs to another lane"):
        lane_kindex.prepare(_lane(tmp_path, "tester"), home)


def test_a_copy_carrying_a_store_or_an_interrupted_home_is_refused(
    tmp_path: Path, kindex: Callable[..., Path]
) -> None:
    kindex()
    lane = _lane(tmp_path, "coder")
    (lane / ".kin" / "local" / "kindex").mkdir(parents=True)
    (lane / ".kin" / "local" / "kindex" / "kindex.db").write_bytes(b"operator graph")
    with pytest.raises(SystemExit, match="already holds a store"):
        lane_kindex.prepare(lane, tmp_path / "home")
    shutil.rmtree(lane / ".kin" / "local")
    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    (interrupted / "partial").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="interrupted launch"):
        lane_kindex.prepare(lane, interrupted)


def test_kinbase_on_the_server_path_and_a_missing_install_are_refused(
    tmp_path: Path, kindex: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    kindex()
    bindir = tmp_path / "sys-bin"
    bindir.mkdir()
    (bindir / "kinbase").write_text("#!/bin/sh\n", encoding="utf-8")
    (bindir / "kinbase").chmod(0o755)
    monkeypatch.setattr(lane_kindex, "SERVER_PATH", (str(bindir),))
    with pytest.raises(SystemExit, match="live Kinbase"):
        lane_kindex.prepare(_lane(tmp_path, "coder"), tmp_path / "home")

    monkeypatch.delenv("FACTORY_KINDEX_LITE_BIN")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(SystemExit, match=r"kindex-lite.*not found"):
        lane_kindex.prepare(_lane(tmp_path, "tester"), tmp_path / "home2")


def test_a_malformed_seed_is_refused(tmp_path: Path, kindex: Callable[..., Path]) -> None:
    kindex()
    seed = tmp_path / "lane-seed.jsonl"
    seed.write_text("not json\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="seed line 1"):
        lane_kindex.prepare(_lane(tmp_path, "coder"), tmp_path / "home", seed)


def test_cli_prints_one_json_record(tmp_path: Path, kindex: Callable[..., Path],
                                    capsys: pytest.CaptureFixture[str]) -> None:
    kindex()
    lane = _lane(tmp_path, "coder")
    assert lane_kindex.main(["prepare", "--repo", str(lane), "--home", str(tmp_path / "h")]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == "factory-lane-kindex/2"
