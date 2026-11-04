"""Endgame uses retained target argv and a separate Factory isolation proof.

The existing harness fixture supplies ledger-backed Stage R/E target binding and
an evidence-producing resident Orchestrator stand-in. A copied trusted Factory
has a recording Makefile; real make must execute it. These tests verify host
gate routing and refusal, not kernel isolation.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_harness_scripts import (
    HARNESS,
    _factory_cli_env,
    _refusal_events,
    execution_truth_fixture,
    install_resident_orchestrator_stub,
    read_chain,
    run,
)


@dataclass
class Endgame:
    directory: Path
    root: Path
    sha: str
    state: dict[str, object]
    env: dict[str, str]
    target_log: Path
    host_log: Path
    replacement_marker: Path
    literal: str
    subpath: str
    ambient_make_log: Path
    harness: Path = HARNESS

    def execute(self) -> subprocess.CompletedProcess[str]:
        return run(
            ["bash", str(self.harness / "endgame.sh"), "r1", self.sha,
             "--candidate-resource", "target-source"],
            self.directory,
            self.env,
        )


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []


def _fixture(
    tmp_path: Path,
    *,
    build: bool = True,
    test: bool = True,
    fail_stage: str = "",
    host_exit: int = 0,
    subpath: str = "apps/widget",
    mutate_retained: bool = False,
    mutate_host_file: str = "",
    target_make: bool = False,
) -> Endgame:
    target_log = tmp_path / "target-executions.jsonl"
    host_log = tmp_path / "host-make.jsonl"
    replacement = tmp_path / "replacement-executed"
    ambient_make_log = tmp_path / "ambient-make-ran"
    host = tmp_path / "trusted-factory"
    shutil.copytree(
        HARNESS.parent, host,
        ignore=shutil.ignore_patterns(".git", ".venv", ".factory", ".harness", "__pycache__",
                                     ".mypy_cache", ".ruff_cache", ".pytest_cache"),
    )
    host_recorder = tmp_path / "host-recorder"
    host_recorder.write_text(
        "import json, os, sys\n"
        f"with open({str(host_log)!r}, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'cwd': os.getcwd(), 'python': sys.argv[1], "
        "'make_env': {name: os.environ.get(name) for name in "
        "('MAKEFLAGS', 'MAKEFILES', 'MFLAGS', 'GNUMAKEFLAGS')}}) + '\\n')\n"
        f"raise SystemExit({host_exit})\n"
    )
    (host / "Makefile").write_text(
        "test-isolation:\n"
        f"\t@{shlex.quote(sys.executable)} {shlex.quote(str(host_recorder))} '$(PY)'\n"
    )
    mutate_host_path = host / mutate_host_file if mutate_host_file else None
    literal = f"spaces ; $(touch {replacement}) `touch {replacement}` $HOME *.data"
    retained = tmp_path / ".factory/runs/r1/evidence/target-resolution/target-manifest.toml"
    script = (
        "import json, os, pathlib, sys\n"
        "stage, log, literal, fail, retained = sys.argv[1:]\n"
        "with open(log, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'stage': stage, 'cwd': os.getcwd(), "
        "'literal': literal}) + '\\n')\n"
        "if stage == 'test' and retained != '-':\n"
        "    with open(retained, 'a', encoding='utf-8') as stream:\n"
        "        stream.write('\\n# candidate changed retained authority\\n')\n"
        + (f"if stage == 'test':\n"
           f"    with open({str(mutate_host_path)!r}, 'a', encoding='utf-8') as stream:\n"
           "        stream.write('\\n# target drifted trusted proof source\\n')\n"
           if mutate_host_path else "") +
        "raise SystemExit(19 if stage == fail else 0)\n"
    )
    prefix = f"{subpath}/" if subpath else ""
    manifest_path = tmp_path / "authorized-target.toml"
    manifest = (HARNESS.parent / "tests/fixtures/synthetic_target/target.toml").read_text()
    if subpath:
        manifest = manifest.replace('[repo]\n', f'[repo]\nsubpath = {json.dumps(subpath)}\n')
    commands = []
    for stage, enabled in (("build", build), ("test", test)):
        if enabled:
            argv = [sys.executable, "verify-tool", stage, str(target_log), literal,
                    fail_stage or "none", str(retained) if mutate_retained else "-"]
            if target_make and stage == "test":
                make = shutil.which("make", path=os.defpath)
                assert make is not None
                argv = [make, "test"]
            commands.append(f"{stage}_command = {json.dumps(argv)}\n")
    manifest = manifest.replace('[build]\n', '[build]\n' + ''.join(commands))
    manifest_path.write_text(manifest)
    candidate_manifest = manifest.replace("verify-tool", "replacement-tool")
    makefile = f"ship test-isolation:\n\t@touch {shlex.quote(str(replacement))}\n"
    if target_make:
        makefile = (
            f"test:\n\t@touch {shlex.quote(str(tmp_path / 'target-make-ran'))}\n\t@exit 17\n"
        )
    _, root, state = execution_truth_fixture(
        tmp_path,
        target_manifest=manifest_path,
        terminal_resources=False,
        extra_blobs={
            prefix + "verify-tool": script.encode(),
            prefix + "replacement-tool": f"open({str(replacement)!r}, 'w').close()\n".encode(),
            prefix + ".factory/target-manifest.toml": candidate_manifest.encode(),
            prefix + "Makefile": makefile.encode(),
        },
    )
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "make").write_text(
        "#!/bin/sh\n"
        f"touch {shlex.quote(str(ambient_make_log))}\nexit 0\n"
    )
    (stub / "make").chmod(0o755)
    install_resident_orchestrator_stub(stub, root)
    conf = tmp_path / "target.conf"
    conf.write_text("provision: true\nprobe: alive:: true\n")
    env = _factory_cli_env()
    env.update({
        "PATH": f"{stub}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HARNESS_TARGET_CONF": str(conf),
        "TEST_CMD": f"touch {shlex.quote(str(replacement))}",
        "BUILD_CMD": f"touch {shlex.quote(str(replacement))}",
        "HARNESS_TEST_CMD": f"touch {shlex.quote(str(replacement))}",
        "HARNESS_BUILD_CMD": f"touch {shlex.quote(str(replacement))}",
    })
    return Endgame(tmp_path, root, str(state["resolved_commit"]), state, env,
                   target_log, host_log, replacement, literal, subpath, ambient_make_log,
                   host / "harness")


def _assert_no_close(fixture: Endgame, result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert "endgame verdict: GREEN" not in result.stdout
    assert json.loads((fixture.root / "harness.json").read_text())["status"] == "open"
    assert not list((fixture.root / "endgame").glob("gate-l-*.json"))
    assert not (fixture.root / "promotion_verdict.json").exists()
    assert "Gate L: NOT RUN" in result.stdout or not (
        fixture.root / "endgame/verdict.json").exists(), (result.stdout, result.stderr)


@pytest.mark.parametrize("subpath", ["", "apps/widget"])
def test_endgame_runs_retained_literal_target_commands_in_exact_fresh_subpath(
    tmp_path: Path, subpath: str,
) -> None:
    fixture = _fixture(tmp_path, subpath=subpath)

    result = fixture.execute()

    rows = _rows(fixture.target_log)
    assert [row["stage"] for row in rows] == ["build", "test"], (result.stdout, result.stderr)
    (fresh,) = (fixture.root / "endgame").glob("endgame-checkout-*")
    assert all(row["cwd"] == str(fresh / subpath) for row in rows)
    assert all(row["literal"] == fixture.literal for row in rows)
    assert Path(str(fixture.state["source_root"])) != fresh
    assert not fixture.replacement_marker.exists()
    assert not fixture.ambient_make_log.exists()
    assert "full gate suite: GREEN" in result.stdout, (result.stdout, result.stderr)
    receipts = read_chain(fixture.root.parent.parent / "receipts/chain.jsonl")
    assert any("verify-tool build" in str(row["cmd"]) for row in receipts)
    assert any("verify-tool test" in str(row["cmd"]) for row in receipts)
    assert all(row["exit"] == 0 for row in receipts)


def test_endgame_omits_only_an_undeclared_optional_build(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, build=False)

    result = fixture.execute()

    assert [row["stage"] for row in _rows(fixture.target_log)] == ["test"], (
        result.stdout, result.stderr)
    assert "full gate suite: GREEN" in result.stdout
    assert not fixture.replacement_marker.exists()


def test_endgame_runs_host_isolation_from_factory_with_factory_interpreter(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    result = fixture.execute()

    host_rows = _rows(fixture.host_log)
    assert len(host_rows) == 1, (host_rows, result.stdout, result.stderr)
    assert host_rows[0]["cwd"] == str(fixture.harness.parent)
    assert host_rows[0]["python"] == sys.executable
    assert "isolation proof: GREEN" in result.stdout
    assert not fixture.replacement_marker.exists()
    assert not fixture.ambient_make_log.exists()


@pytest.mark.parametrize("stage", ["build", "test"])
def test_endgame_target_command_failure_blocks_admission_and_has_a_receipt(
    tmp_path: Path, stage: str,
) -> None:
    fixture = _fixture(tmp_path, fail_stage=stage)

    result = fixture.execute()

    assert stage in [row["stage"] for row in _rows(fixture.target_log)], (
        result.stdout, result.stderr)
    _assert_no_close(fixture, result)
    assert "full gate suite: RED" in result.stdout
    receipts = read_chain(fixture.root.parent.parent / "receipts/chain.jsonl")
    assert any(f"verify-tool {stage}" in str(row["cmd"]) and row["exit"] == 19
               for row in receipts)
    assert not fixture.replacement_marker.exists()


def test_endgame_ambient_make_cannot_replace_a_failing_trusted_host_proof(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, host_exit=23)

    result = fixture.execute()

    assert _rows(fixture.host_log), (result.stdout, result.stderr)
    _assert_no_close(fixture, result)
    assert "isolation proof: RED" in result.stdout
    assert not fixture.ambient_make_log.exists()
    receipts = read_chain(fixture.root.parent.parent / "receipts/chain.jsonl")
    assert any("test-isolation" in str(row["cmd"]) and row["exit"] != 0
               for row in receipts)


def test_endgame_refuses_missing_retained_test_command(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, test=False)

    result = fixture.execute()

    _assert_no_close(fixture, result)
    assert not fixture.target_log.exists()
    assert _refusal_events(fixture.root), (result.stdout, result.stderr)
    assert not fixture.replacement_marker.exists()


def test_endgame_refuses_changed_retained_manifest_before_target_execution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    retained = fixture.root / "evidence/target-resolution/target-manifest.toml"
    retained.write_bytes(retained.read_bytes() + b"\n# unauthorized replacement\n")

    result = fixture.execute()

    _assert_no_close(fixture, result)
    assert not fixture.target_log.exists()
    assert _refusal_events(fixture.root), (result.stdout, result.stderr)


def test_endgame_rechecks_retained_manifest_after_target_commands(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, mutate_retained=True)

    result = fixture.execute()

    assert [row["stage"] for row in _rows(fixture.target_log)] == ["build", "test"], (
        result.stdout, result.stderr)
    _assert_no_close(fixture, result)
    assert "candidate changed retained authority" in (
        fixture.root / "evidence/target-resolution/target-manifest.toml").read_text()


def test_endgame_host_isolation_cannot_inherit_make_noop_or_injected_makefiles(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.env.update({"MAKEFLAGS": "-n", "MAKEFILES": str(tmp_path / "injected.mk"),
                        "MFLAGS": "-n", "GNUMAKEFLAGS": "-n"})

    result = fixture.execute()

    (host,) = _rows(fixture.host_log)
    flags = shlex.split(str(host["make_env"].get("MAKEFLAGS") or ""))
    # make propagates legitimate command-line assignments alongside its option flags.
    assert all("n" not in flag.lstrip("-") for flag in flags if "=" not in flag), (
        result.stdout, result.stderr)
    assert "n" not in str(host["make_env"].get("MFLAGS") or "")
    assert host["make_env"].get("MAKEFILES") in (None, "")
    assert host["make_env"].get("GNUMAKEFLAGS") in (None, "")
    assert not fixture.ambient_make_log.exists()
    receipts = read_chain(fixture.root.parent.parent / "receipts/chain.jsonl")
    assert any("test-isolation" in str(row["cmd"]) and row["exit"] == 0
               for row in receipts)


@pytest.mark.parametrize("host_file", ["pyproject.toml", "tests/conftest.py"])
def test_endgame_rechecks_factory_proof_sources_after_target_execution(
    tmp_path: Path, host_file: str,
) -> None:
    fixture = _fixture(tmp_path, mutate_host_file=host_file)

    result = fixture.execute()

    assert [row["stage"] for row in _rows(fixture.target_log)] == ["build", "test"], (
        result.stdout, result.stderr)
    assert "target drifted trusted proof source" in (fixture.harness.parent / host_file).read_text()
    _assert_no_close(fixture, result)


def test_endgame_target_make_dry_run_cannot_skip_a_failing_retained_recipe(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, build=False, target_make=True)
    fixture.env["MAKEFLAGS"] = "-n"

    result = fixture.execute()

    assert (tmp_path / "target-make-ran").exists(), (result.stdout, result.stderr)
    _assert_no_close(fixture, result)
    assert "full gate suite: RED" in result.stdout
    assert "isolation proof: GREEN" in result.stdout
    assert not fixture.ambient_make_log.exists()
    receipts = read_chain(fixture.root.parent.parent / "receipts/chain.jsonl")
    assert any("make test" in str(row["cmd"]) and row["exit"] != 0 for row in receipts)
