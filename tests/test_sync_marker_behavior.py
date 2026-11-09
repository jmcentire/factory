"""Independent real-rsync checks; candidate source must remain unread.

FACTORY_UNDER_TEST selects the checkout. RAW_RSYNC_CONTROL runs deletion checks
against bare rsync as the negative control.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

MARKER = ".kindex-no-index"
TARGET = Path(os.environ.get("FACTORY_UNDER_TEST", str(Path(__file__).resolve().parents[1])))

OBSERVER = r"""
import json
import os
from pathlib import Path
import subprocess
import sys
marker = Path(os.environ["SYNC_OBSERVER_DEST"]) / ".kindex-no-index"
before = marker.exists()
fd = os.open(marker, os.O_RDONLY) if before else None
initial = os.fstat(fd) if fd is not None else None
result = subprocess.run(["/usr/bin/rsync", *sys.argv[1:]], check=False)
after = marker.stat() if marker.exists() else None
same = bool(initial and after and (initial.st_dev, initial.st_ino) == (after.st_dev, after.st_ino))
with open(os.environ["SYNC_OBSERVER_LOG"], "a") as log:
    event = {"before": before, "same_marker": same, "status": result.returncode}
    log.write(json.dumps(event) + "\n")
if fd is not None:
    os.close(fd)
sys.exit(result.returncode)
"""


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def environment(tmp_path, destination):
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir(exist_ok=True)
    bindir.mkdir(exist_ok=True)
    observer = bindir / "rsync"
    observer.write_text(f"#!{sys.executable}\n" + OBSERVER)
    observer.chmod(0o700)
    return {
        "HOME": str(home),
        "PATH": f"{bindir}:{Path(sys.executable).parent}:/usr/bin:/bin:/opt/homebrew/bin",
        "TMPDIR": str(tmp_path),
        "FACTORY_HERALD": "off",
        "FACTORY_PYTHON": sys.executable,
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "SYNC_OBSERVER_DEST": str(destination),
        "SYNC_OBSERVER_LOG": str(tmp_path / "rsync.jsonl"),
    }


def cli(env, operation, *args):
    return subprocess.run(
        [sys.executable, "-m", "factory_runtime.kindex_workspace", operation, *map(str, args)],
        cwd=TARGET,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def assert_observed_protection(env):
    events = [json.loads(line) for line in Path(env["SYNC_OBSERVER_LOG"]).read_text().splitlines()]
    assert events, "real rsync did not execute"
    assert all(event["before"] for event in events), "rsync started before workspace protection"
    assert all(event["same_marker"] for event in events), "rsync removed/replaced exclusion marker"


@pytest.mark.parametrize("preexisting", [False, True], ids=["missing-marker", "existing-marker"])
def test_deletion_sync_preserves_exclusion_and_source_bytes(tmp_path, preexisting):
    source, destination, home = tmp_path / "source", tmp_path / "destination", tmp_path / "home"
    for directory in [source, destination, home]:
        directory.mkdir()
    (source / "product.txt").write_bytes(b"new product content\n")
    (source / "nested").mkdir()
    (source / "nested" / "second.txt").write_bytes(b"second product\n")
    (destination / "stale-product.txt").write_bytes(b"must be removed by --delete\n")
    marker_bytes = b"existing private exclusion metadata\n"
    if preexisting:
        (destination / MARKER).write_bytes(marker_bytes)
    before = snapshot(source)
    env = environment(tmp_path, destination)
    if os.environ.get("RAW_RSYNC_CONTROL"):
        result = subprocess.run(
            ["rsync", "-a", "--delete", str(source) + "/", str(destination) + "/"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        result = cli(env, "sync", "--source", source, "--destination", destination, "--delete")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (destination / "stale-product.txt").exists(), "delete synchronization was bypassed"
    for name, content in before.items():
        assert (destination / name).read_bytes() == content
    assert snapshot(source) == before, "sync modified canonical source"
    assert not (source / MARKER).exists(), "sync incorrectly excluded canonical source"
    assert (destination / MARKER).is_file(), "sync left transient worktree indexable"
    if preexisting:
        assert (destination / MARKER).read_bytes() == marker_bytes
    assert_observed_protection(env)


def test_sender_markers_are_not_copied_and_user_excludes_preserve_receiver_marker(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / MARKER).write_bytes(b"sender identity must not overwrite receiver\n")
    (source / "nested").mkdir()
    (source / "nested" / MARKER).write_bytes(b"nested sender marker\n")
    (source / "nested/product.txt").write_bytes(b"accepted bytes\n")
    (source / "omit.txt").write_bytes(b"caller excludes me\n")
    (destination / MARKER).write_bytes(b"receiver metadata\n")
    before = snapshot(source)
    env = environment(tmp_path, destination)
    result = cli(
        env,
        "sync",
        "--source",
        source,
        "--destination",
        destination,
        "--delete",
        "--exclude",
        "omit.txt",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / MARKER).read_bytes() == b"receiver metadata\n"
    assert not (destination / "nested" / MARKER).exists()
    assert (destination / "nested/product.txt").read_bytes() == b"accepted bytes\n"
    assert not (destination / "omit.txt").exists()
    assert snapshot(source) == before
    assert_observed_protection(env)


@pytest.mark.parametrize("preexisting", [False, True], ids=["missing-marker", "existing-marker"])
def test_repeated_scratch_reset_keeps_root_and_exclusion(tmp_path, preexisting):
    source, tests, destination = tmp_path / "source", tmp_path / "tests-input", tmp_path / "scratch"
    for directory in [source, tests, destination]:
        directory.mkdir()
    (source / "product.txt").write_bytes(b"product bytes\n")
    (tests / "tests").mkdir()
    (tests / "tests/test_contract.py").write_bytes(b"TESTER INPUT\n")
    (tests / "not-a-test.txt").write_bytes(b"not requested\n")
    (source / ".factory").mkdir()
    (source / ".factory/private.json").write_bytes(b"private source runtime data\n")
    env = environment(tmp_path, destination)
    for repository in [source, tests]:
        subprocess.run(["git", "init", "-q", str(repository)], env=env, check=True)
    original_source, original_tests = snapshot(source), snapshot(tests)
    marker_bytes = b"private original identity\n"
    if preexisting:
        (destination / MARKER).write_bytes(marker_bytes)
    root_fd = os.open(destination, os.O_RDONLY)
    marker_fd = os.open(destination / MARKER, os.O_RDONLY) if preexisting else None
    try:
        initial_root = os.fstat(root_fd)
        for iteration in range(2):
            (destination / "stale.txt").write_text(f"old scratch {iteration}")
            result = cli(
                env,
                "prepare-scratch",
                "--source",
                source,
                "--tests",
                tests,
                "--destination",
                destination,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert not (destination / "stale.txt").exists()
            assert (destination / "product.txt").read_bytes() == b"product bytes\n"
            assert (destination / "tests/test_contract.py").read_bytes() == b"TESTER INPUT\n"
            assert not (destination / "not-a-test.txt").exists()
            assert not (destination / ".git").exists()
            assert not (destination / ".factory").exists()
            assert destination.stat().st_ino == initial_root.st_ino, "reset removed workspace root"
            assert (destination / MARKER).is_file()
            if marker_fd is None:
                marker_fd = os.open(destination / MARKER, os.O_RDONLY)
            else:
                retained_marker, current_marker = os.fstat(marker_fd), (destination / MARKER).stat()
                assert (current_marker.st_dev, current_marker.st_ino) == (
                    retained_marker.st_dev,
                    retained_marker.st_ino,
                ), "reset removed or replaced the exclusion marker before rsync"
            if preexisting:
                assert (destination / MARKER).read_bytes() == marker_bytes
    finally:
        if marker_fd is not None:
            os.close(marker_fd)
        os.close(root_fd)
    assert snapshot(source) == original_source
    assert snapshot(tests) == original_tests
    assert_observed_protection(env)


@pytest.mark.parametrize(
    "relationship", ["same", "destination-in-source", "source-in-destination", "symlink"]
)
def test_unsafe_sync_aliases_refuse_without_mutation(tmp_path, relationship):
    source = tmp_path / "source"
    source.mkdir()
    (source / "canonical.txt").write_bytes(b"canonical data\n")
    if relationship == "same":
        destination = source
    elif relationship == "destination-in-source":
        destination = source / "child"
        destination.mkdir()
    elif relationship == "source-in-destination":
        destination = source
        source = destination / "child"
        source.mkdir()
        (source / "nested.txt").write_bytes(b"nested source\n")
    else:
        destination = tmp_path / "alias"
        destination.symlink_to(source, target_is_directory=True)
    observed_root = tmp_path / "source"
    before = snapshot(observed_root)
    env = environment(tmp_path, destination)
    result = cli(env, "sync", "--source", source, "--destination", destination, "--delete")
    assert result.returncode != 0, "unsafe overlapping synchronization was accepted"
    assert snapshot(observed_root) == before
    assert not (observed_root / MARKER).exists()
    assert not Path(env["SYNC_OBSERVER_LOG"]).exists(), "rsync ran before rejecting unsafe roots"


def test_real_transfer_failure_keeps_exclusion(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("unreadable-file failure requires an unprivileged process")
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    secret = source / "unreadable.txt"
    secret.write_bytes(b"source bytes must survive refusal\n")
    (destination / MARKER).write_bytes(b"must survive failed transfer\n")
    env = environment(tmp_path, destination)
    secret.chmod(0)
    try:
        result = cli(env, "sync", "--source", source, "--destination", destination, "--delete")
    finally:
        secret.chmod(0o600)
    assert result.returncode != 0, "real rsync failure was hidden"
    assert secret.read_bytes() == b"source bytes must survive refusal\n"
    assert (destination / MARKER).read_bytes() == b"must survive failed transfer\n"
    assert_observed_protection(env)


def test_scratch_same_tree_inputs_remain_supported(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    (source / "product.txt").write_bytes(b"source data\n")
    (source / "tests").mkdir()
    (source / "tests/test_contract.txt").write_bytes(b"test data\n")
    before = snapshot(source)
    env = environment(tmp_path, destination)
    result = cli(
        env, "prepare-scratch", "--source", source, "--tests", source, "--destination", destination
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(source) == before
    for name, content in before.items():
        assert (destination / name).read_bytes() == content
    assert_observed_protection(env)


@pytest.mark.parametrize("kind", ["flake", "mutate"])
@pytest.mark.parametrize("preexisting", [False, True], ids=["missing-marker", "existing-marker"])
def test_lifecycle_scripts_keep_exclusion_at_actual_test_execution(tmp_path, kind, preexisting):
    tree = tmp_path / "input"
    (tree / "src/pkg").mkdir(parents=True)
    (tree / "src/pkg/__init__.py").write_text("def guarded():\n    return 'safe'\n")
    (tree / "tests").mkdir()
    (tree / "tests/test_guard.py").write_text(
        "from pathlib import Path\nfrom pkg import guarded\n\n"
        "def test_guard():\n"
        "    assert Path('.kindex-no-index').is_file()\n"
        "    assert guarded() == 'safe'\n"
    )
    before = snapshot(tree)
    scratch_parent = tmp_path / "scratch-parent"
    destination = scratch_parent / "case"
    if preexisting:
        destination.mkdir(parents=True)
        (destination / MARKER).write_bytes(b"original lifecycle identity\n")
        (destination / "stale.txt").write_bytes(b"remove during reset\n")
    env = environment(tmp_path, destination)
    env["MUTATE_WORKDIR"] = str(scratch_parent)
    command = ["bash", str(TARGET / "harness" / (kind + ".sh")), "case"]
    if kind == "mutate":
        patch = tmp_path / "patch.py"
        patch.write_text(
            "import sys\nfrom pathlib import Path\n"
            "p = Path(sys.argv[1]) / 'src/pkg/__init__.py'\n"
            "assert Path(sys.argv[1], '.kindex-no-index').is_file()\n"
            "p.write_text(p.read_text().replace(\"return 'safe'\", \"return 'broken'\"))\n"
        )
        command += [str(patch), "--named-test", "tests/test_guard.py::test_guard"]
    else:
        command += ["--runs", "2"]
    test_command = shlex.quote(sys.executable) + " -m pytest tests/ -q -p no:cacheprovider"
    command += ["--src", str(tree), "--tests", str(tree), "--test-cmd", test_command]
    result = subprocess.run(
        command, cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / MARKER).is_file()
    if preexisting:
        assert (destination / MARKER).read_bytes() == b"original lifecycle identity\n"
    assert not (destination / "stale.txt").exists()
    assert snapshot(tree) == before
    assert_observed_protection(env)


def test_synced_generated_knowledge_exports_without_runtime_marker(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "transient"
    (source / ".kin").mkdir(parents=True)
    knowledge = b'{"title":"generated conclusion","audience":"team"}\n'
    (source / ".kin/knowledge.jsonl").write_bytes(knowledge)
    (source / "product.txt").write_bytes(b"accepted implementation\n")
    env = environment(tmp_path, destination)
    subprocess.run(["git", "init", "-q", str(source)], env=env, check=True)
    result = cli(env, "sync", "--source", source, "--destination", destination, "--delete")
    assert result.returncode == 0, result.stdout + result.stderr
    frozen = subprocess.run(
        [
            sys.executable,
            "-m",
            "harness.lane_repository",
            "freeze",
            "--source",
            str(destination),
            "--store",
            str(tmp_path / "exports"),
            "--durable-through",
            str(tmp_path),
        ],
        cwd=TARGET,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert frozen.returncode == 0, frozen.stdout + frozen.stderr
    accepted = Path(json.loads(frozen.stdout)["files_directory"])
    assert not list(accepted.rglob(MARKER)), "runtime exclusion leaked into accepted product"
    assert (accepted / ".kin/knowledge.jsonl").read_bytes() == knowledge
    assert (accepted / "product.txt").read_bytes() == b"accepted implementation\n"
    assert (destination / MARKER).is_file()
    assert not (source / MARKER).exists()


@pytest.mark.parametrize("pattern", ["+ .kindex-no-index", "!"])
@pytest.mark.parametrize(
    "receiver_marker", [False, True], ids=["missing-marker", "existing-marker"]
)
@pytest.mark.parametrize(
    "sender_marker", [False, True], ids=["no-sender-marker", "conflicting-sender"]
)
def test_legacy_exclude_syntax_cannot_disable_marker_protection(
    tmp_path, pattern, sender_marker, receiver_marker
):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "product.txt").write_bytes(b"new product\n")
    if sender_marker:
        (source / MARKER).write_bytes(b"conflicting sender metadata\n")
    if receiver_marker:
        (destination / MARKER).write_bytes(b"receiver private identity\n")
    (destination / "stale.txt").write_bytes(b"old product\n")
    before = snapshot(source)
    destination_before = tree_state(destination)
    env = environment(tmp_path, destination)
    result = cli(
        env,
        "sync",
        "--source",
        source,
        "--destination",
        destination,
        "--delete",
        "--exclude",
        pattern,
    )
    if pattern == "!":
        assert result.returncode != 0, "unsafe filter-list clearing must be rejected"
        assert tree_state(destination) == destination_before
        assert snapshot(source) == before
        assert not Path(env["SYNC_OBSERVER_LOG"]).exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    if receiver_marker:
        assert (destination / MARKER).read_bytes() == b"receiver private identity\n"
    else:
        assert (destination / MARKER).is_file()
        assert (destination / MARKER).read_bytes() != b"conflicting sender metadata\n"
    assert (destination / "product.txt").read_bytes() == b"new product\n"
    assert not (destination / "stale.txt").exists()
    assert snapshot(source) == before
    assert_observed_protection(env)


def tree_state(root):
    """Record directory entries as well as bytes so rejected work cannot create scratch paths."""
    return snapshot(root), sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


@pytest.mark.parametrize("operation", ["sync", "prepare-scratch"])
@pytest.mark.parametrize(
    "relationship", ["same", "missing-destination-child", "destination-parent"]
)
def test_case_alias_overlap_is_refused_without_touching_inputs(tmp_path, operation, relationship):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    alias = tmp_path / "CANONICAL"
    if not alias.exists() or not os.path.samefile(canonical, alias):
        pytest.skip("case-alias control requires a case-insensitive filesystem")
    (canonical / "important.txt").write_bytes(b"canonical bytes\n")
    tests = tmp_path / "test-input"
    (tests / "tests").mkdir(parents=True)
    (tests / "tests/test_contract.txt").write_bytes(b"private tester bytes\n")
    source = canonical
    if relationship == "same":
        destination = alias
    elif relationship == "missing-destination-child":
        destination = alias / "missing" / "scratch"
    else:
        source = canonical / "child"
        source.mkdir()
        (source / "input.txt").write_bytes(b"nested source bytes\n")
        destination = alias
    before, tests_before = tree_state(canonical), tree_state(tests)
    env = environment(tmp_path, destination)
    args = ["--source", source, "--destination", destination]
    args += ["--delete"] if operation == "sync" else ["--tests", tests]
    result = cli(env, operation, *args)
    assert result.returncode != 0, "physical input overlap was accepted"
    assert tree_state(canonical) == before, "rejected alias changed canonical input"
    assert tree_state(tests) == tests_before, "rejected alias changed private test input"
    assert not list(canonical.rglob(MARKER)), "rejected alias marked canonical input"
    assert not list(tests.rglob(MARKER)), "rejected alias marked private test input"
    assert not Path(env["SYNC_OBSERVER_LOG"]).exists(), "rsync ran on overlapping inputs"


@pytest.mark.parametrize("preexisting_marker", [False, True])
def test_scratch_reset_refuses_existing_repository(tmp_path, preexisting_marker):
    source, tests, destination = (
        tmp_path / "source",
        tmp_path / "tests-input",
        tmp_path / "unrelated",
    )
    source.mkdir()
    (source / "product.txt").write_bytes(b"new product\n")
    (tests / "tests").mkdir(parents=True)
    (tests / "tests/test_contract.txt").write_bytes(b"tester input\n")
    env = environment(tmp_path, destination)
    subprocess.run(["git", "init", "-q", str(destination)], env=env, check=True)
    (destination / "uncommitted.txt").write_bytes(b"unrelated user work\n")
    if preexisting_marker:
        (destination / MARKER).write_bytes(b"existing unrelated exclusion\n")
    before = tree_state(destination)
    source_before, tests_before = tree_state(source), tree_state(tests)
    result = cli(
        env, "prepare-scratch", "--source", source, "--tests", tests, "--destination", destination
    )
    assert result.returncode != 0, "scratch reset accepted an unrelated repository"
    assert tree_state(destination) == before, "scratch reset modified unrelated repository"
    assert tree_state(source) == source_before
    assert tree_state(tests) == tests_before
    assert not Path(env["SYNC_OBSERVER_LOG"]).exists()
