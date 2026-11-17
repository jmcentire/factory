"""A refused author launch names every path that caused the refusal, in one message.

Authority: normative. Cites the operator incident (2026-11-17): an npm install in a lane
clone left `node_modules` directory links and husky's `.husky/_`; the refusal named
neither, finding the cause took ~6 minutes, and fixing the first cause exposed a second
refusal. The stated contract, as revised the same day: directory links and non-regular
files are named in sorted string order (at most five each, then " and <N> more");
unrecorded changes are named as added, removed and changed paths; every cause appears in
one refusal; receipts carry per-file digests for every file the tree digest covers, the
`.kindex-no-index` marker included, while `tree_digest` values stay what they were;
receipts prepared before this change still verify and never name the marker.

Expected values are direct from that contract unless a comment says "inferred".
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from harness import role_inputs, run_record

REPO = Path(__file__).resolve().parents[1]
# The last commit before refusals named paths: the oracle for unchanged tree digests and
# for receipts prepared by the earlier code.
BEFORE_PATHS = "ecddf63b6d1cfd83fb290c488bed1ca43279ae3d"
MARKER = ".kindex-no-index"
LINKS = "role inputs contain a directory symlink: "
ODD = "role inputs contain a non-regular file: "
ODD_PART = "; a non-regular file: "
CHANGES = "role inputs have unrecorded changes before first launch: "
BOTH = "; and unrecorded changes: "
NO_DIGESTS = "file contents (this receipt has no per-file digests)"

MODULES = [f"src/mod{index}.py" for index in range(7)]
PAGES = [f"docs/page{index}.md" for index in range(7)]
# The Coder's projection of the fixture source: everything but tests/ and .kin/.
CODER_FILES = sorted(["api/alias", "api/spec.txt", "src/app.py", *MODULES, *PAGES])


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def inputs(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    contents = {"src/app.py": "IMPLEMENTATION\n", "tests/test_app.py": "ORACLE\n",
                "api/spec.txt": "PUBLIC_BEHAVIOR\n", ".kin/knowledge.jsonl": "PRIVATE_MEMORY\n"}
    contents.update({name: f"{name}\n" for name in MODULES + PAGES})
    for name, value in contents.items():
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(value)
    (repo / "api/alias").symlink_to("spec.txt")
    _commit(repo, "source")
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repo, b"Implement public behavior")
    return repo, runs, root


@pytest.fixture
def coder(tmp_path, inputs):
    """A prepared Coder projection that verifies before anything touches it."""
    _, runs, root = inputs
    dest = tmp_path / "coder"
    result = role_inputs.prepare(runs, "task", "coder", dest, [], ["tests"])
    assert sorted(result["files"]) == CODER_FILES
    role_inputs.verify(runs, "task", "coder", dest)  # positive control: clean verifies
    return dest, runs, root, result


def _refusal(runs, dest, role="coder"):
    with pytest.raises(ValueError) as refused:
        role_inputs.verify(runs, "task", role, dest)
    return str(refused.value)


def _link_dir(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target, target_is_directory=True)
    assert path.is_symlink() and path.is_dir()  # it is a directory link, not a file link


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _drop_file_digests(root, role="coder"):
    """Rewrite the receipt as a receipt prepared before per-file digests existed."""
    path = root / "inputs" / f"{role}.json"
    receipt = json.loads(path.read_text())
    receipt.pop("file_digests")
    path.write_text(json.dumps(receipt, indent=2) + "\n")


@pytest.fixture(scope="module")
def before(tmp_path_factory):
    """harness/role_inputs.py as committed before this change, loaded beside the current one."""
    source = subprocess.run(["git", "-C", str(REPO), "show",
                             f"{BEFORE_PATHS}:harness/role_inputs.py"],
                            check=True, capture_output=True).stdout
    path = tmp_path_factory.mktemp("before") / "role_inputs_before_paths.py"
    path.write_bytes(source)
    # A module under the harness package imports harness.run_record, as the real one does.
    spec = importlib.util.spec_from_file_location("harness._role_inputs_before_paths", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- receipts -------------------------------------------------------------------------------


def test_receipt_has_a_short_digest_for_every_digested_file_marker_included(tmp_path, coder):
    # Revised contract: file_digests covers every file tree_digest covers, the marker too.
    dest, runs, root, result = coder
    assert (dest / MARKER).is_file()  # the marker is really in the projection
    digests = result["file_digests"]
    assert sorted(digests) == sorted([*CODER_FILES, MARKER])
    for value in digests.values():
        assert re.fullmatch(r"[0-9a-fA-F]{16}", value)
    on_disk = json.loads((root / "inputs/coder.json").read_text())
    assert on_disk["file_digests"] == digests
    empty = role_inputs.prepare(runs, "task", "tester", tmp_path / "tester", [], [])
    assert empty["files"] == [] and list(empty["file_digests"]) == [MARKER]
    role_inputs.verify(runs, "task", "tester", tmp_path / "tester")


@pytest.mark.parametrize("change,expected", [
    ("delete", "removed " + MARKER), ("rewrite", "changed " + MARKER)])
def test_a_deleted_or_rewritten_marker_is_named(coder, change, expected):
    # `git clean -fdx` in a lane clone deletes the marker: it is listed in .git/info/exclude.
    dest, runs, _, _ = coder
    if change == "delete":
        (dest / MARKER).unlink()
    else:
        (dest / MARKER).write_text("rewritten\n")
    assert _refusal(runs, dest) == CHANGES + expected


# --- directory links ------------------------------------------------------------------------


def test_every_directory_link_is_named_and_a_link_alone_gives_only_that(coder):
    dest, runs, _, _ = coder
    # Created out of order across several directories; the refusal names them sorted.
    for name in ("src/zz_link", "node_modules/b", "api/link", "node_modules/a"):
        _link_dir(dest / name, dest / "docs")
    message = LINKS + "api/link, node_modules/a, node_modules/b, src/zz_link"
    assert _refusal(runs, dest) == message
    with pytest.raises(ValueError) as refused:
        role_inputs.tree_digest(dest)
    assert str(refused.value) == message


@pytest.mark.parametrize("links,named", [
    # A walk that names a directory's own links before its subdirectories' gives "b, api/link".
    (["b", "api/link"], "api/link, b"),
    # A walk that descends into "a" before reaching the entry "a.b" gives "a/x, a.b".
    (["a/x", "a.b"], "a.b, a/x"),
])
def test_directory_links_are_named_in_plain_string_order(coder, links, named):
    dest, runs, _, _ = coder
    (dest / "a").mkdir()
    for name in links:
        _link_dir(dest / name, dest / "src")
    assert _refusal(runs, dest) == LINKS + named


@pytest.mark.parametrize("count,named,rest", [
    (5, 5, ""), (6, 5, " and 1 more"), (8, 5, " and 3 more")])
def test_at_most_five_directory_links_are_named(coder, count, named, rest):
    dest, runs, _, _ = coder
    names = [f"node_modules/pkg{index}" for index in range(count)]
    for name in reversed(names):
        _link_dir(dest / name, "../src")
    assert _refusal(runs, dest) == LINKS + ", ".join(names[:named]) + rest


# --- unrecorded changes ---------------------------------------------------------------------


@pytest.mark.parametrize("change,expected", [
    ("add", "added api/new.txt, zz_new.txt"),
    ("remove", "removed docs/page3.md, src/mod1.py"),
    ("content", "changed src/app.py"),
    ("mode", "changed api/spec.txt"),
    ("link target", "changed api/alias"),
    ("add+content", "added api/new.txt, zz_new.txt; changed src/app.py"),
    ("remove+mode", "removed docs/page3.md, src/mod1.py; changed api/spec.txt"),
    ("add+remove+content+mode+link target",
     "added api/new.txt, zz_new.txt; removed docs/page3.md, src/mod1.py; "
     "changed api/alias, api/spec.txt, src/app.py"),
])
def test_unrecorded_changes_name_each_path_in_its_part(coder, change, expected):
    dest, runs, _, _ = coder
    kinds = change.split("+")
    if "add" in kinds:
        # A root file is walked before api/, so only sorting puts api/new.txt first.
        _write(dest / "zz_new.txt", "new")
        _write(dest / "api/new.txt", "new")
    if "remove" in kinds:
        (dest / "src/mod1.py").unlink()
        (dest / "docs/page3.md").unlink()
    if "content" in kinds:
        (dest / "src/app.py").write_text("CHANGED\n")
    if "mode" in kinds:
        spec = dest / "api/spec.txt"
        spec.chmod(spec.stat().st_mode ^ 0o100)
    if "link target" in kinds:
        (dest / "api/alias").unlink()
        (dest / "api/alias").symlink_to("other.txt")
    assert _refusal(runs, dest) == CHANGES + expected


def test_each_part_names_at_most_five_paths(coder):
    dest, runs, _, _ = coder
    added = [f"extra/f{index}" for index in range(6)]
    for name in added:
        _write(dest / name, "new")
    for name in PAGES:
        (dest / name).unlink()
    for name in MODULES[:5]:
        (dest / name).write_text("CHANGED\n")
    assert _refusal(runs, dest) == CHANGES + (
        "added " + ", ".join(added[:5]) + " and 1 more; "
        "removed " + ", ".join(PAGES[:5]) + " and 2 more; "
        "changed " + ", ".join(MODULES[:5]))


# --- both at once ---------------------------------------------------------------------------


def test_npm_install_leftovers_are_named_in_one_refusal(coder):
    """The incident: one refusal names the links and the files an install left behind."""
    dest, runs, _, _ = coder
    _write(dest / "node_modules/real/cli.js", "cli")
    (dest / "node_modules/.bin").mkdir()
    (dest / "node_modules/.bin/tool").symlink_to("../real/cli.js")  # a file link is a file
    _link_dir(dest / "node_modules/linked", "real")
    _write(dest / ".husky/_/h", "hook")
    (dest / "src/app.py").write_text("CHANGED\n")
    assert _refusal(runs, dest) == (
        LINKS + "node_modules/linked" + BOTH
        + "added .husky/_/h, node_modules/.bin/tool, node_modules/real/cli.js; "
        "changed src/app.py")


def test_a_directory_replaced_by_a_link_names_the_link_and_what_it_hid(coder):
    # Inferred: the files under a directory replaced by a link are no longer in the tree,
    # so they are removed, an effect beyond the link itself.
    dest, runs, _, _ = coder
    for name in PAGES:
        (dest / name).unlink()
    (dest / "docs").rmdir()
    _link_dir(dest / "docs", "src")
    assert _refusal(runs, dest) == (
        LINKS + "docs" + BOTH + "removed " + ", ".join(PAGES[:5]) + " and 2 more")


# --- HEAD and non-regular files -------------------------------------------------------------


def test_a_moved_head_names_both_commits(coder):
    dest, runs, _, result = coder
    initial = result["initial_commit"]
    _commit(dest, "lane commit")
    head = _git(dest, "rev-parse", "HEAD")
    assert head != initial
    message = (f"role inputs changed before first launch: HEAD is {head[:12]}, "
               f"not the projection's {initial[:12]}")
    assert _refusal(runs, dest) == message
    # Inferred: HEAD is the first thing compared, so committed work is reported the same way.
    (dest / "src/app.py").write_text("CHANGED\n")
    _commit(dest, "lane work")
    head = _git(dest, "rev-parse", "HEAD")
    assert _refusal(runs, dest) == (
        f"role inputs changed before first launch: HEAD is {head[:12]}, "
        f"not the projection's {initial[:12]}")


def test_a_non_regular_file_alone_is_named(coder):
    dest, runs, _, _ = coder
    os.mkfifo(dest / "api/pipe")
    assert _refusal(runs, dest) == ODD + "api/pipe"


@pytest.mark.parametrize("count,named,rest", [
    (5, 5, ""), (6, 5, " and 1 more"), (8, 5, " and 3 more")])
def test_at_most_five_non_regular_files_are_named(coder, count, named, rest):
    dest, runs, _, _ = coder
    (dest / "run").mkdir()
    names = [f"run/pipe{index}" for index in range(count)]
    for name in reversed(names):
        os.mkfifo(dest / name)
    assert _refusal(runs, dest) == ODD + ", ".join(names[:named]) + rest


@pytest.mark.parametrize("others,expected", [
    ("link", LINKS + "node_modules/linked" + ODD_PART + "api/pipe, zz_pipe"),
    ("added", ODD + "api/pipe, zz_pipe" + BOTH + "added .husky/_/h"),
    ("link+added",
     LINKS + "node_modules/linked" + ODD_PART + "api/pipe, zz_pipe" + BOTH + "added .husky/_/h"),
])
def test_non_regular_files_are_named_with_every_other_cause(coder, others, expected):
    dest, runs, _, _ = coder
    # A root FIFO is walked before api/, so only sorting puts api/pipe first. A FIFO is not
    # an added file: a FIFO alone gives only the non-regular part.
    os.mkfifo(dest / "zz_pipe")
    os.mkfifo(dest / "api/pipe")
    if "link" in others.split("+"):
        _link_dir(dest / "node_modules/linked", "../src")
    if "added" in others.split("+"):
        _write(dest / ".husky/_/h", "hook")
    assert _refusal(runs, dest) == expected
    with pytest.raises(ValueError) as refused:
        role_inputs.tree_digest(dest)
    assert str(refused.value) == expected.split(BOTH)[0]  # tree_digest names no changes


# --- receipts prepared before this change ---------------------------------------------------


@pytest.mark.parametrize("change,expected", [
    ("none", None),
    ("add", "added leaked.py"),
    ("remove", "removed src/mod1.py"),
    ("add+remove", "added leaked.py; removed src/mod1.py"),
    # The receipt's files list never held the marker, so a missing marker is not named.
    ("marker+add", "added leaked.py"),
    ("marker+remove", "removed src/mod1.py"),
])
def test_a_receipt_without_file_digests_still_names_added_and_removed(coder, change, expected):
    dest, runs, root, _ = coder
    _drop_file_digests(root)
    assert "file_digests" not in json.loads((root / "inputs/coder.json").read_text())
    kinds = change.split("+")
    if "add" in kinds:
        _write(dest / "leaked.py", "IMPLEMENTATION")
    if "remove" in kinds:
        (dest / "src/mod1.py").unlink()
    if "marker" in kinds:
        (dest / MARKER).unlink()
    if expected is None:
        role_inputs.verify(runs, "task", "coder", dest)
    else:
        # Inferred: when added or removed paths explain the change, no contents part follows.
        assert _refusal(runs, dest) == CHANGES + expected


@pytest.mark.parametrize("change", ["content", "marker deleted"])
def test_a_receipt_without_file_digests_says_only_contents_changed(coder, change):
    # Inferred for "marker deleted": the tree digest still differs, so the launch is still
    # refused, and with nothing the receipt can name the refusal is the contents form.
    dest, runs, root, _ = coder
    _drop_file_digests(root)
    if change == "content":
        (dest / "src/app.py").write_text("CHANGED\n")
    else:
        (dest / MARKER).unlink()
    message = _refusal(runs, dest)
    assert message.startswith(CHANGES) and message.endswith(NO_DIGESTS)
    assert MARKER not in message  # the marker is never named from an older receipt


def test_a_receipt_prepared_by_the_earlier_code_verifies_and_names_changes(tmp_path, inputs,
                                                                          before):
    _, runs, _ = inputs
    dest = tmp_path / "coder"
    receipt = before.prepare(runs, "task", "coder", dest, [], ["tests"])
    assert "file_digests" not in receipt
    role_inputs.verify(runs, "task", "coder", dest)
    _write(dest / "node_modules/real/cli.js", "cli")
    (dest / "src/mod1.py").unlink()
    _link_dir(dest / "node_modules/linked", "real")
    assert _refusal(runs, dest) == (
        LINKS + "node_modules/linked" + BOTH
        + "added node_modules/real/cli.js; removed src/mod1.py")


# --- tree digests are unchanged -------------------------------------------------------------


def _pinned_tree(path):
    rows = {".git/HEAD": (b"ref: refs/heads/lane/coder\n", 0o644), MARKER: (b"", 0o600),
            "README.md": (b"# fixture\n", 0o644), "bin/run.sh": (b"#!/bin/sh\nexit 0\n", 0o755),
            "empty": (b"", 0o600), "nested/deep/data.bin": (bytes(range(256)), 0o644)}
    for name, (data, mode) in rows.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    return path


def test_tree_digest_is_the_digest_the_earlier_code_computed(tmp_path, coder, before):
    dest, _, _, result = coder
    # Positive control: this is the earlier module, whose refusal named no path.
    link = dest / "node_modules/linked"
    _link_dir(link, "../src")
    with pytest.raises(ValueError) as refused:
        before.tree_digest(dest)
    assert str(refused.value) == "role inputs contain a directory symlink"
    link.unlink()
    (dest / "node_modules").rmdir()
    assert result["tree_digest"] == before.tree_digest(dest) == role_inputs.tree_digest(dest)
    tree = _pinned_tree(tmp_path / "tree")
    (tree / "nested/alias").symlink_to("deep/data.bin")
    (tree / "dangling").symlink_to("missing")
    first = before.tree_digest(tree)
    assert role_inputs.tree_digest(tree) == first
    (tree / "nested/alias").unlink()
    (tree / "nested/alias").symlink_to("../README.md")
    assert before.tree_digest(tree) != first  # the comparison is sensitive to a link target
    assert role_inputs.tree_digest(tree) == before.tree_digest(tree)


def test_tree_digest_pin(tmp_path):
    # Pin (T9): computed by tree_digest at BEFORE_PATHS. Receipts already written depend on it.
    assert role_inputs.tree_digest(_pinned_tree(tmp_path / "tree")) == (
        "b11045b9b68357e20ba61e57d18df14d0582be78c28a0d783c6ca640926e015e")


# --- what the operator sees -----------------------------------------------------------------


def _environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    env.update(HOME=str(home), PYTHONPATH=str(REPO), FACTORY_PYTHON=sys.executable)
    return env


def test_verify_command_exits_70_with_the_named_paths(tmp_path, coder):
    dest, runs, _, _ = coder
    command = [sys.executable, str(REPO / "harness/role_inputs.py"), "verify", "--runs",
               str(runs), "--run", "task", "--role", "coder", "--repo", str(dest)]
    clean = subprocess.run(command, env=_environment(tmp_path), capture_output=True, text=True)
    assert clean.returncode == 0, clean.stderr
    _link_dir(dest / "node_modules/linked", "../src")
    _write(dest / ".husky/_/h", "hook")
    refused = subprocess.run(command, env=_environment(tmp_path), capture_output=True,
                             text=True)
    assert refused.returncode == 70
    assert ("role inputs: " + LINKS + "node_modules/linked" + BOTH + "added .husky/_/h"
            in refused.stderr.splitlines())


def test_the_launcher_shows_the_named_paths(tmp_path, coder):
    dest, runs, _, _ = coder
    _link_dir(dest / "node_modules/linked", "../src")
    brief = tmp_path / "brief"
    brief.write_text("Implement behavior")
    result = subprocess.run(["bash", str(REPO / "harness/tmux_lane.sh"), "task", "coder",
                             "launch", "--runs", str(runs), "--repo", str(dest), "--prompt",
                             str(brief)], env=_environment(tmp_path), capture_output=True,
                            text=True)
    assert result.returncode != 0
    assert "role inputs: " + LINKS + "node_modules/linked" in result.stderr.splitlines()
    assert "prepared role projection" in result.stderr
