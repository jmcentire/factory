"""Contained committed file links must remain exact and usable by agreement probes."""
from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_agreement_contract import agreement_fixture, create_probe_candidate, run_probe


def commit(repo: Path) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "file links"], cwd=repo, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "harness"))
    return importlib.import_module("agreement_probe")


def test_linked_suite_reaches_both_commands_and_binds_its_target(tmp_path: Path, probe) -> None:
    root, _ = agreement_fixture(tmp_path, [("R1.1", ["hold", "quote"], "Quote and hold agree.")])
    repo, _ = create_probe_candidate(tmp_path)
    (repo / "local_test.py").rename(repo / "local_real.py")
    (repo / "local_test.py").symlink_to("local_real.py")
    sha = commit(repo)
    extracted = tmp_path / "extracted"
    probe.extract_candidate(repo, sha, extracted)
    assert os.readlink(extracted / "local_test.py") == "local_real.py"
    assert (extracted / "local_real.py").read_bytes() == (repo / "local_real.py").read_bytes()
    before = probe.file_manifest(extracted, ["local_test.py"])
    (extracted / "local_real.py").write_text("changed\n")
    assert probe.file_manifest(extracted, ["local_test.py"]) != before
    witness = run_probe(tmp_path, root, repo, sha, "producer")
    assert witness["local_suite_digest"] and witness["agreement_oracle_digest"]


@pytest.mark.parametrize("target", ["/etc/passwd", "../outside", "missing", "link",
                                   "other", "folder", "folder/../../outside", "folder/"])
def test_unsafe_links_refuse_before_extraction_and_manifest(tmp_path: Path, probe, target) -> None:
    repo, _ = create_probe_candidate(tmp_path)
    (repo / "folder").mkdir()
    (repo / "folder" / "file").write_text("inside")
    (repo / "link").symlink_to(target)
    (repo / "other").symlink_to("link")
    sha = commit(repo)
    with pytest.raises(probe.ProbeError):
        probe.extract_candidate(repo, sha, tmp_path / "extracted")
    assert not (tmp_path / "extracted").exists()
    with pytest.raises(probe.ProbeError):
        probe.file_manifest(repo, ["link"])


def test_parent_relative_link_and_link_text_are_bound(tmp_path: Path, probe) -> None:
    repo, _ = create_probe_candidate(tmp_path)
    (repo / "folder").mkdir()
    (repo / "folder" / "link").symlink_to("../impl.py")
    sha = commit(repo)
    extracted = tmp_path / "extracted"
    probe.extract_candidate(repo, sha, extracted)
    before = probe.file_manifest(extracted, ["folder"])
    (extracted / "folder" / "link").unlink()
    (extracted / "folder" / "link").symlink_to(".././impl.py")
    assert probe.file_manifest(extracted, ["folder"]) != before
    (extracted / "escape").symlink_to(tmp_path)
    with pytest.raises(probe.ProbeError):
        probe.file_manifest(extracted, ["escape/extracted/impl.py"])
