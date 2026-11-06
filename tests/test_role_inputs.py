"""Input independence is about reachable bytes, not two different directory names."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness import role_inputs, run_record


@pytest.fixture
def inputs(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    contents = {"src/app.py": "IMPLEMENTATION_SECRET", "tests/test_app.py": "ORACLE_SECRET",
                "api/spec.txt": "PUBLIC_BEHAVIOR", ".kin/knowledge.jsonl": "PRIVATE_MEMORY"}
    for name, value in contents.items():
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(value)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "source"], check=True)
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repo, b"Implement public behavior")
    return repo, runs, root


def test_filtered_files_and_history_preserve_dirty_source(tmp_path, inputs):
    source, runs, _ = inputs
    (source / "src/app.py").write_text("user's unrelated dirty work")
    coder, tester = tmp_path / "coder", tmp_path / "tester"
    role_inputs.prepare(runs, "task", "coder", coder, [], ["tests"])
    role_inputs.prepare(runs, "task", "tester", tester, ["api"], [])
    assert (coder / "src/app.py").read_text() == "IMPLEMENTATION_SECRET"
    assert not (coder / "tests").exists()
    assert not (tester / "src").exists()
    assert (tester / "api/spec.txt").read_text() == "PUBLIC_BEHAVIOR"
    for repo, hidden in ((coder, "ORACLE_SECRET"), (tester, "IMPLEMENTATION_SECRET")):
        assert not (repo / ".kin").exists()
        history = subprocess.run(["git", "-C", str(repo), "log", "--all", "-p"],
                                 capture_output=True, text=True, check=True).stdout
        assert hidden not in history
        assert not (repo / ".git/objects/info/alternates").exists()
    assert (source / "src/app.py").read_text() == "user's unrelated dirty work"
    role_inputs.verify(runs, "task", "tester", tester)
    (tester / "leaked.py").write_text("implementation")
    with pytest.raises(ValueError, match="unrecorded changes"):
        role_inputs.verify(runs, "task", "tester", tester)


def test_empty_tester_tree_needs_only_behavioral_brief(tmp_path, inputs):
    _, runs, _ = inputs
    dest = tmp_path / "tester"
    result = role_inputs.prepare(runs, "task", "tester", dest, [], [])
    assert result["files"] == []
    role_inputs.verify(runs, "task", "tester", dest)


def test_git_ignored_content_cannot_hide_contaminated_inputs(tmp_path, inputs):
    _, runs, _ = inputs
    dest = tmp_path / "tester"
    role_inputs.prepare(runs, "task", "tester", dest, [], [])
    (dest / ".git/info/exclude").write_text("private-source\n")
    (dest / "private-source").write_text("implementation")
    with pytest.raises(ValueError, match="unrecorded changes"):
        role_inputs.verify(runs, "task", "tester", dest)


@pytest.mark.parametrize("path", ["../src", "/src", ".", "api/../src", ".git", "api//spec.txt"])
def test_invalid_selection_does_not_create_author_tree(tmp_path, inputs, path):
    _, runs, _ = inputs
    dest = tmp_path / "tester"
    with pytest.raises(ValueError):
        role_inputs.prepare(runs, "task", "tester", dest, [path], [])
    assert not dest.exists()


def test_public_symlink_cannot_expose_withheld_source(tmp_path, inputs):
    source, runs, root = inputs
    (source / "api/leak").symlink_to("../src/app.py")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "link"], check=True)
    # A new task pins the new commit; retained task inputs are not edited.
    run_record.create(runs, "linked", source, b"Implement behavior")
    with pytest.raises(ValueError, match="withheld input"):
        role_inputs.prepare(runs, "linked", "tester", tmp_path / "tester", ["api"], [])


def _commit_links(source, runs):
    """An in-tree directory link (wandercom/wander's .claude/skills shape) and an escaping one."""
    (source / ".agents/skills/x").mkdir(parents=True)
    (source / ".agents/skills/x/SKILL.md").write_text("SKILL")
    (source / ".claude/skills").mkdir(parents=True)
    (source / ".claude/skills/x").symlink_to("../../.agents/skills/x")
    (source / ".claude/skills/y").symlink_to("../../.agents/skills/x/")
    (source / "outside").symlink_to("../../escape")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm", "links"], check=True)
    run_record.create(runs, "linked", source, b"Implement behavior")


def test_link_through_an_omitted_directory_is_refused_not_left_dangling(tmp_path, inputs):
    source, runs, _ = inputs
    (source / "real").mkdir()
    (source / "real/f").write_text("F")
    (source / "dir").mkdir()
    (source / "dir/link").symlink_to("../real")
    (source / "alias").symlink_to("dir/../real/f")
    _commit_links(source, runs)
    with pytest.raises(ValueError, match="withheld input: alias"):
        role_inputs.prepare(runs, "linked", "coder", tmp_path / "coder", [], ["tests", "outside"])


def test_unincluded_directory_link_is_omitted_and_recorded(tmp_path, inputs):
    source, runs, _ = inputs
    _commit_links(source, runs)
    coder, tester = tmp_path / "coder", tmp_path / "tester"
    result = role_inputs.prepare(runs, "linked", "coder", coder, [], ["tests", "outside"])
    assert result["omitted_directory_links"] == [".claude/skills/x", ".claude/skills/y"]
    assert (coder / ".agents/skills/x/SKILL.md").read_text() == "SKILL"
    assert ".claude/skills/x" not in result["files"] and "outside" not in result["files"]
    assert not (coder / ".claude").exists() and not (coder / "outside").is_symlink()
    role_inputs.verify(runs, "linked", "coder", coder)
    role_inputs.prepare(runs, "linked", "tester", tester, ["api"], [])
    role_inputs.verify(runs, "linked", "tester", tester)


@pytest.mark.parametrize("role,link,refusal", [
    ("coder", "outside", "escaping"), ("tester", "outside", "escaping"),
    ("tester", ".claude/skills/x", "link to a directory"),
    ("tester", ".claude", "link to a directory")])
def test_selected_directory_or_escaping_link_is_refused(tmp_path, inputs, role, link, refusal):
    source, runs, _ = inputs
    _commit_links(source, runs)
    includes, excludes = ([link], []) if role == "tester" else ([], ["tests"])
    dest = tmp_path / role
    with pytest.raises((RuntimeError, ValueError), match=refusal):
        role_inputs.prepare(runs, "linked", role, dest, includes, excludes)
    assert not dest.exists()


@pytest.mark.parametrize("subtree", [True, False])
def test_malformed_tree_with_a_link_and_an_entry_at_one_path_is_refused(tmp_path, inputs, subtree):
    source, runs, _ = inputs

    def git(*args, stdin=None):
        return subprocess.run(["git", "-C", str(source), *args], input=stdin, check=True,
                              capture_output=True, text=True).stdout.strip()

    blob = git("hash-object", "-w", "--stdin", stdin="F")
    link = git("hash-object", "-w", "--stdin", stdin="real")
    real = git("mktree", stdin=f"100644 blob {blob}\tf\n")
    rows = [f"040000 tree {real}\treal", f"120000 blob {link}\td"]
    rows.append(f"040000 tree {real}\td" if subtree else f"100644 blob {blob}\td")
    tree = git("mktree", stdin="\n".join(rows) + "\n")
    git("reset", "-q", "--soft", git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                 "commit-tree", tree, "-m", "malformed"))
    run_record.create(runs, "malformed", source, b"Implement behavior")
    with pytest.raises(RuntimeError, match="duplicate or file-and-directory"):
        role_inputs.prepare(runs, "malformed", "coder", tmp_path / "coder", [], [])


def test_unknown_or_missing_selection_is_not_silently_empty(tmp_path, inputs):
    _, runs, _ = inputs
    with pytest.raises(ValueError, match="absent"):
        role_inputs.prepare(runs, "task", "tester", tmp_path / "tester", ["apis"], [])


def test_launcher_refuses_full_clone_before_contacting_agent(tmp_path, inputs):
    source, runs, _ = inputs
    brief = tmp_path / "brief"
    brief.write_text("Implement behavior")
    tool = Path(__file__).resolve().parents[1] / "harness/tmux_lane.sh"
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG") if key in os.environ}
    env["FACTORY_PYTHON"] = sys.executable
    result = subprocess.run(["bash", str(tool), "task", "tester", "launch", "--runs", str(runs),
                             "--repo", str(source), "--prompt", str(brief)], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "prepared role projection" in result.stderr
