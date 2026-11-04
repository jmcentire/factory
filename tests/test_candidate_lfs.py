"""Exact-candidate validation with local LFS data; no providers or native agents."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import run_record, validate_candidate
from tests.test_host_git import lfs_pointer, lfs_repository

CONTENT = b"asset bytes whose digest is pinned by the candidate\n"
OID = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def task(tmp_path):
    repository, commit = lfs_repository(tmp_path, {"art/board.png": lfs_pointer(CONTENT)})
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repository, b"Validate the exact asset")
    return repository, commit, runs, root


def stock(store, content=CONTENT):
    path = store / OID[:2] / OID[2:4] / OID
    path.parent.mkdir(parents=True)
    path.write_bytes(content)


def checks(expected):
    return [sys.executable, "-c", "from pathlib import Path; "
            f"assert Path('art/board.png').read_bytes() == {expected!r}"]


def evidence(result):
    return json.loads((result / "result.json").read_text())


@pytest.mark.parametrize("source", ["candidate", "original", "explicit"])
def test_local_asset_bytes_are_verified_and_the_store_is_recorded(task, tmp_path, source):
    repository, commit, runs, _ = task
    candidate = repository
    options = {}
    if source == "original":
        candidate = tmp_path / "candidate"
        subprocess.run(["git", "clone", "-q", "--no-checkout", str(repository), str(candidate)],
                       check=True)
    store = repository / ".git/lfs/objects"
    if source == "explicit":
        store = tmp_path / "explicit-store"
        options["lfs_objects"] = store
    stock(store)
    code, result = validate_candidate.validate(runs, "task", candidate, commit,
                                               checks(CONTENT), require_lfs=True, **options)
    record = evidence(result)
    assert code == 0
    assert record["candidate"]["commit"] == commit
    assert record["candidate"]["lfs_hydrated"] == 1
    assert record["candidate"]["lfs_pointers_left"] == 0
    assert record["candidate"]["lfs_bytes"] == len(CONTENT)
    assert record["lfs"]["objects"] == str(store.resolve())
    assert "accepted" not in record


@pytest.mark.parametrize(
    "problem", ["missing-store", "empty-store", "corrupt-object", "attributes"],
)
def test_required_content_failure_cannot_run_checks_or_report_pass(task, tmp_path, problem):
    repository, commit, runs, _ = task
    marker = tmp_path / "checks-ran"
    store = repository / ".git/lfs/objects"
    if problem == "empty-store":
        store.mkdir(parents=True)
    elif problem == "corrupt-object":
        stock(store, b"x" * len(CONTENT))
    elif problem == "attributes":
        stock(store)
        (repository / ".git/info/attributes").write_text("* !filter\n")
    code, result = validate_candidate.validate(
        runs, "task", repository, commit,
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"], require_lfs=True,
    )
    record = evidence(result)
    assert code == 70 and record["status"] == "validation-failed"
    assert record["error"] and not marker.exists()
    if problem in {"missing-store", "attributes"}:
        assert record["candidate"]["lfs_pointers_left"] == 1
        assert "requires LFS content" in record["error"]
    else:
        assert "candidate" not in record


def test_discovered_incomplete_store_is_not_silently_ignored(task):
    repository, commit, runs, _ = task
    (repository / ".git/lfs/objects").mkdir(parents=True)
    code, result = validate_candidate.validate(runs, "task", repository, commit, checks(CONTENT))
    assert code == 70
    assert "not in the LFS store" in evidence(result)["error"]


def test_pointer_only_validation_reports_its_limitation_through_endgame_cli(task):
    repository, commit, runs, root = task
    environment = dict(os.environ, FACTORY_PYTHON=sys.executable)
    process = subprocess.run(
        ["bash", str(Path(validate_candidate.__file__).with_name("endgame.sh")), "task", commit,
         "--repo", str(repository), "--runs", str(runs), "--", *checks(lfs_pointer(CONTENT))],
        env=environment, capture_output=True, text=True,
    )
    assert process.returncode == 0, process.stderr
    assert "hydrated 0 file(s), 0 bytes; 1 pointer file(s) remain" in process.stdout
    result, = (root / "validation").iterdir()
    assert evidence(result)["lfs"] == {"required": False, "objects": None}


def test_literal_pointer_example_is_preserved_when_attributes_do_not_select_it(tmp_path):
    repository, commit = lfs_repository(tmp_path, {"art/board.png": lfs_pointer(CONTENT)},
                                       attributes="*.png -filter\n")
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repository, b"Check a literal pointer fixture")
    stock(repository / ".git/lfs/objects")
    code, result = validate_candidate.validate(runs, "task", repository, commit,
                                               checks(lfs_pointer(CONTENT)))
    assert code == 0
    assert evidence(result)["candidate"]["lfs_pointers_left"] == 1
    assert evidence(result)["candidate"]["lfs_hydrated"] == 0


def test_endgame_required_lfs_option_prevents_check_execution(task, tmp_path):
    repository, commit, runs, root = task
    marker = tmp_path / "checks-ran"
    process = subprocess.run(
        ["bash", str(Path(validate_candidate.__file__).with_name("endgame.sh")), "task", commit,
         "--repo", str(repository), "--runs", str(runs), "--require-lfs", "--",
         sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
        env=dict(os.environ, FACTORY_PYTHON=sys.executable), capture_output=True, text=True,
    )
    assert process.returncode == 1 and "FAIL:" in process.stdout
    assert "1 pointer file(s) remain" in process.stdout
    assert "PASS:" not in process.stdout and not marker.exists()
    result, = (root / "validation").iterdir()
    assert evidence(result)["status"] == "validation-failed"


def test_invalid_lfs_option_is_not_silently_accepted(task):
    repository, commit, runs, root = task
    process = subprocess.run(
        ["bash", str(Path(validate_candidate.__file__).with_name("endgame.sh")), "task", commit,
         "--repo", str(repository), "--runs", str(runs), "--require-lfs=maybe", "--",
         *checks(CONTENT)],
        env=dict(os.environ, FACTORY_PYTHON=sys.executable), capture_output=True, text=True,
    )
    assert process.returncode == 2 and "--require-lfs" in process.stderr
    assert "PASS:" not in process.stdout and not (root / "validation").exists()
