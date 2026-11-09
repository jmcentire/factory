"""Independent Factory behavior checks; no candidate implementation imports/reads.

Run with FACTORY_UNDER_TEST=/path/to/checkout python -m pytest this_file.
All subprocesses receive a temporary HOME and all products stay in tmp_path.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TARGET = Path(os.environ.get("FACTORY_UNDER_TEST", str(Path(__file__).resolve().parents[1])))
MARKER = ".kindex-no-index"
WRITE_ORDER_AUDIT = r"""
import os
from pathlib import Path
import runpy
import sys

def audit(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes)):
        return
    path = Path(os.fsdecode(args[0]))
    flags = args[2]
    if not isinstance(flags, int) or not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
        return
    if path.name not in {"harness.json", "app.txt", "contract.txt", "knowledge.jsonl", "kindex.db"}:
        return
    assert any((parent / ".kindex-no-index").is_file() for parent in path.absolute().parents), (
        "substantive workspace write occurred before exclusion: " + str(path))

module = sys.argv[1]
sys.argv = sys.argv[1:]
sys.addaudithook(audit)
runpy.run_module(module, run_name="__main__")
"""


def checked(args, *, cwd, env):
    proc = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    assert proc.returncode == 0, f"{args!r}\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


@pytest.fixture
def workspace(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        "TMPDIR": str(tmp_path),
        "LANG": "en_US.UTF-8",
        "FACTORY_HERALD": "off",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    source = tmp_path / "canonical"
    source.mkdir()
    files = {
        "src/app.txt": "implementation",
        "tests/oracle.txt": "oracle",
        "api/contract.txt": "public contract",
        ".kin/knowledge.jsonl": "shared context",
    }
    for name, value in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    checked(["git", "init", "-q", str(source)], cwd=tmp_path, env=env)
    checked(["git", "add", "."], cwd=source, env=env)
    checked(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Initial public contract",
        ],
        cwd=source,
        env=env,
    )
    task = tmp_path / "task.txt"
    task.write_text("Implement the public contract while retaining independent lane knowledge")
    runs = tmp_path / "records"

    def cli(module, *args):
        full_module = module if "." in module else "harness." + module
        prefix = (
            [sys.executable, "-c", WRITE_ORDER_AUDIT]
            if env.get("CHECK_WRITE_ORDER")
            else [sys.executable, "-m"]
        )
        return checked([*prefix, full_module, *map(str, args)], cwd=TARGET, env=env)

    cli(
        "run_record",
        "create",
        "--runs",
        runs,
        "--run",
        "isolated",
        "--repo",
        source,
        "--task-file",
        task,
    )
    return {
        "home": home,
        "env": env,
        "source": source,
        "files": files,
        "runs": runs,
        "root": runs / "isolated",
        "tmp": tmp_path,
        "cli": cli,
    }


def prepare_lanes(workspace):
    w = workspace
    lanes = {}
    for role, selection in (("coder", ["--exclude", "tests"]), ("tester", ["--include", "api"])):
        repo = w["tmp"] / role
        w["cli"](
            "role_inputs",
            "prepare",
            "--runs",
            w["runs"],
            "--run",
            "isolated",
            "--role",
            role,
            "--repo",
            repo,
            *selection,
        )
        w["cli"](
            "role_inputs",
            "verify",
            "--runs",
            w["runs"],
            "--run",
            "isolated",
            "--role",
            role,
            "--repo",
            repo,
        )
        lanes[role] = repo
    return lanes


def test_public_projection_positive_control(workspace):
    w = workspace
    lanes = prepare_lanes(w)
    assert (lanes["coder"] / "src/app.txt").read_text() == "implementation"
    assert not (lanes["coder"] / "tests/oracle.txt").exists()
    assert (lanes["tester"] / "api/contract.txt").read_text() == "public contract"
    assert not (lanes["tester"] / "src/app.txt").exists()
    for repo in lanes.values():
        assert not (repo / ".kin/knowledge.jsonl").exists()
    for name, value in w["files"].items():
        assert (w["source"] / name).read_text() == value
    assert checked(["git", "status", "--porcelain"], cwd=w["source"], env=w["env"]) == ""


@pytest.mark.parametrize("subject", ["run", "coder", "tester"])
def test_new_run_and_each_author_repository_are_excluded(workspace, subject):
    w = workspace
    lanes = prepare_lanes(w)
    subject_root = w["root"] if subject == "run" else lanes[subject]
    assert (subject_root / MARKER).is_file(), f"new {subject} root remains indexable"
    for repo in lanes.values():
        assert (repo / MARKER).is_file(), "standalone author tree remains indexable"
        tracked = checked(["git", "ls-files"], cwd=repo, env=w["env"]).splitlines()
        assert MARKER not in tracked, "runtime exclusion contaminates product history"
        assert not checked(["git", "status", "--porcelain"], cwd=repo, env=w["env"])
    assert not (w["source"] / MARKER).exists(), "canonical source was incorrectly excluded"


def test_generated_knowledge_survives_product_freeze(workspace):
    w = workspace
    lane = prepare_lanes(w)["coder"]
    (lane / ".kin").mkdir(exist_ok=True)
    knowledge = b'{"title":"Fresh author conclusion","audience":"team"}\n'
    (lane / ".kin/knowledge.jsonl").write_bytes(knowledge)
    (lane / MARKER).write_text("workspace exclusion must never enter accepted product\n")
    (lane / "api" / MARKER).write_text("nested runtime marker\n")
    output = w["cli"](
        "lane_repository",
        "freeze",
        "--source",
        lane,
        "--store",
        w["root"] / "exports",
        "--durable-through",
        w["root"],
    )
    result = json.loads(output)
    exported = Path(result["files_directory"])
    assert (exported / ".kin/knowledge.jsonl").read_bytes() == knowledge
    assert not (exported / ".git").exists()
    assert not (exported / MARKER).exists(), (
        "accepted snapshot incorrectly excludes canonical project"
    )
    assert not (exported / "api" / MARKER).exists(), "nested runtime marker leaked into product"
    assert (lane / MARKER).is_file(), "export removed exclusion from the original worktree"
    assert (w["source"] / ".kin/knowledge.jsonl").read_text() == "shared context"


@pytest.mark.parametrize("legacy", [False, True], ids=["marked-run", "legacy-run"])
def test_archive_restore_preserves_private_work_product(workspace, legacy):
    w = workspace
    private = w["root"] / "nested-lane/.kin"
    private.mkdir(parents=True)
    original = {
        "knowledge.jsonl": b'{"title":"unmerged insight"}\n',
        "local/kindex/kindex.db": b"opaque lane-local fixture bytes\x00",
    }
    for name, value in original.items():
        path = private / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(value)
    marker_bytes = b"Factory run remains transient after completion\n"
    if legacy:
        (w["root"] / MARKER).unlink(missing_ok=True)
    else:
        (w["root"] / MARKER).write_bytes(marker_bytes)
        (private.parent / MARKER).write_bytes(marker_bytes)
    archives = w["tmp"] / "archives"
    archives.mkdir()
    w["cli"](
        "factory_runtime.cli",
        "cleanup",
        "--runs",
        w["runs"],
        "--run",
        "isolated",
        "--archive",
        "--completed",
        "--archive-dir",
        archives,
        "--apply",
    )
    assert not w["root"].exists()
    (archive,) = archives.glob("*.tar.gz")
    restored = w["tmp"] / "restored"
    w["env"]["CHECK_WRITE_ORDER"] = "1"
    restored_result = json.loads(
        w["cli"](
            "factory_runtime.cli",
            "cleanup",
            "--restore",
            archive,
            "--destination",
            restored,
            "--apply",
        )
    )
    for name, value in original.items():
        assert (restored / "nested-lane/.kin" / name).read_bytes() == value
    assert (restored / MARKER).is_file(), "restored Factory data became indexable"
    assert restored_result["kindex_exclusion_marker_added"] is legacy
    if not legacy:
        assert (restored / MARKER).read_bytes() == marker_bytes
        assert (restored / "nested-lane" / MARKER).read_bytes() == marker_bytes


@pytest.mark.parametrize("scenario", ["ordinary", "symlink-marker", "symlink-root"])
def test_private_kindex_prepare_marks_standalone_clone_without_losing_store(workspace, scenario):
    """Checks Factory's configuration/storage behavior, not real Kindex add/search."""
    w = workspace
    fixture = Path(__file__).parent / "fixtures/kindex_lite_stub.py"
    bindir = w["tmp"] / "private-kindex-fixture"
    bindir.mkdir()
    lite = bindir / "kindex-lite"
    lite.write_text(f"#!{sys.executable}\n" + fixture.read_text())
    lite.chmod(0o700)
    (bindir / "stub-config.json").write_text(
        json.dumps(
            {
                "tools": [
                    "add",
                    "search",
                    "list_nodes",
                    "scope_info",
                    "kinbase_explain",
                    "kinbase_sync",
                ]
            }
        )
    )
    kin = bindir / "kin"
    kin.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --version ]; then echo 'kin 0.48.1 (Kindex)'; exit 0; fi\n"
        'mkdir -p "$KIN_PROJECT_PATH/.kin/local/kindex"\n'
        'if [ "$1" = kinbase ]; then echo \'{"imported":0,"quarantined":0}\'; exit 0; fi\n'
        "echo 'Import complete: 1 created, 0 updated'\n"
    )
    kin.chmod(0o700)
    w["env"]["FACTORY_KINDEX_LITE_BIN"] = str(lite)
    repo = w["tmp"] / "standalone"
    home = w["tmp"] / "lane-private-home"
    if scenario == "symlink-root":
        repo.symlink_to(w["source"], target_is_directory=True)
        before = {
            str(path.relative_to(w["source"])): path.read_bytes()
            for path in w["source"].rglob("*")
            if path.is_file()
        }
        refused = subprocess.run(
            [
                sys.executable,
                "-m",
                "harness.lane_kindex",
                "prepare",
                "--repo",
                str(repo),
                "--home",
                str(home),
            ],
            cwd=TARGET,
            env=w["env"],
            capture_output=True,
            check=False,
        )
        after = {
            str(path.relative_to(w["source"])): path.read_bytes()
            for path in w["source"].rglob("*")
            if path.is_file()
        }
        assert not (w["source"] / MARKER).exists(), "symlink marked canonical source as transient"
        assert after == before, "symlink preparation mutated canonical source"
        assert refused.returncode != 0, "symlinked repository root must be refused"
        assert repo.is_symlink() and repo.readlink() == w["source"]
        return
    checked(
        ["git", "clone", "--quiet", "--no-hardlinks", str(w["source"]), str(repo)],
        cwd=w["tmp"],
        env=w["env"],
    )
    if scenario == "symlink-marker":
        target = w["source"] / ".kin/knowledge.jsonl"
        before = target.read_bytes()
        (repo / MARKER).symlink_to(target)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "harness.lane_kindex",
                "prepare",
                "--repo",
                str(repo),
                "--home",
                str(home),
            ],
            cwd=TARGET,
            env=w["env"],
            capture_output=True,
            check=False,
        )
        assert target.read_bytes() == before, "marker handling overwrote canonical knowledge"
        assert (repo / ".kin/knowledge.jsonl").read_bytes() == before, (
            "marker handling removed lane knowledge"
        )
        assert (repo / MARKER).is_symlink()
        assert (repo / MARKER).readlink() == target
        return
    initial = json.loads(w["cli"]("lane_kindex", "prepare", "--repo", repo, "--home", home))
    store = Path(initial["store"])
    store.mkdir(parents=True, exist_ok=True)
    private_data = store / "independent-private-sentinel"
    private_data.write_bytes(b"locally generated private knowledge")
    w["cli"]("lane_kindex", "prepare", "--repo", repo, "--home", home)
    assert private_data.read_bytes() == b"locally generated private knowledge"
    assert str(repo.resolve()) in initial["mcp_override"]
    assert str(home.resolve()) in initial["mcp_override"]
    assert (repo / MARKER).is_file(), "standalone Kindex lane remains background-indexable"
    assert not (w["source"] / MARKER).exists()


def test_source_runtime_markers_do_not_enter_initial_lane_history(workspace):
    w = workspace
    for marker in [w["source"] / MARKER, w["source"] / "api" / MARKER]:
        marker.write_bytes(b"inherited runtime metadata\n")
    checked(["git", "add", "."], cwd=w["source"], env=w["env"])
    checked(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Inherited runtime markers",
        ],
        cwd=w["source"],
        env=w["env"],
    )
    task = w["tmp"] / "task.txt"
    w["cli"](
        "run_record",
        "create",
        "--runs",
        w["runs"],
        "--run",
        "inherited",
        "--repo",
        w["source"],
        "--task-file",
        task,
    )
    for role, selection in [("coder", ["--exclude", "tests"]), ("tester", ["--include", "api"])]:
        repo = w["tmp"] / ("inherited-" + role)
        w["cli"](
            "role_inputs",
            "prepare",
            "--runs",
            w["runs"],
            "--run",
            "inherited",
            "--role",
            role,
            "--repo",
            repo,
            *selection,
        )
        w["cli"](
            "role_inputs",
            "verify",
            "--runs",
            w["runs"],
            "--run",
            "inherited",
            "--role",
            role,
            "--repo",
            repo,
        )
        tracked = checked(["git", "ls-files"], cwd=repo, env=w["env"]).splitlines()
        assert all(Path(name).name != MARKER for name in tracked)
        assert (repo / MARKER).is_file()
    assert (w["source"] / MARKER).read_bytes() == b"inherited runtime metadata\n"


def test_run_and_projection_writes_are_protected_before_materialization(workspace):
    w = workspace
    w["env"]["CHECK_WRITE_ORDER"] = "1"
    w["cli"](
        "run_record",
        "create",
        "--runs",
        w["runs"],
        "--run",
        "audited",
        "--repo",
        w["source"],
        "--task-file",
        w["tmp"] / "task.txt",
    )
    for role, selection in [("coder", ["--exclude", "tests"]), ("tester", ["--include", "api"])]:
        w["cli"](
            "role_inputs",
            "prepare",
            "--runs",
            w["runs"],
            "--run",
            "audited",
            "--role",
            role,
            "--repo",
            w["tmp"] / ("audited-" + role),
            *selection,
        )
