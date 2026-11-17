"""A refused author launch names every path that caused the refusal, in one message.

Authority: normative. Cites the operator incident (2026-11-17): an install left
`apps/website/node_modules/react` (a directory link) and husky's `.husky/_/` in a lane
clone; the refusal named neither and stopped at the first problem, so the operator needed
two refusal rounds. The contract, as redesigned the same day: the receipt keeps exactly the
keys and `tree_digest` values it had before; the refusal names directory links, non-regular
files, unreadable files and unlistable directories (in that order), then the changes against
the projection's first commit as added, removed and changed paths (sorted, at most five per
kind, then " and N more"); a directory the first commit never had is named once as `dir/`.
Amended the same day after review: prepare writes files at exactly 0644 or 0755, so any mode
change of a committed file is named; contents compare as `git add` would commit them now
(attribute filters included); files under an unlistable directory, or under a directory
replaced by a link or a non-regular file, are not reported as removed; a new directory is
named only if it holds more than directories; an unreadable first commit is said to be
unreadable. Problem kinds are joined by "; " (inferred, as the changes are).

Expected values are direct from that contract unless a comment says "inferred". Digest
literals were computed once by the module as it stood at ecddf63 (before refusals named
paths); nothing here reads Git history at test time.
"""

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from harness import role_inputs, run_record

REPO = Path(__file__).resolve().parents[1]
MARKER = ".kindex-no-index"
CONTAINS = "role inputs contain "
LINKS = CONTAINS + "a directory symlink: "
ODD = CONTAINS + "a non-regular file: "
UNREADABLE = CONTAINS + "a file that cannot be read: "
UNLISTABLE = CONTAINS + "a directory that cannot be read: "
CHANGES = "role inputs have unrecorded changes before first launch: "
BOTH = "; and unrecorded changes: "
UNSHOWN = CHANGES + ("none that the projection's first commit can show "
                     "(line endings Git normalizes, or the no-index marker's mode)")
UNREAD_BASELINE = "the projection's first commit could not be read ("
# The receipt keys harness/role_inputs.py wrote at ecddf63, in its order.
RECEIPT_KEYS = ["role", "repository", "source_commit", "initial_commit", "includes", "excludes",
                "files", "omitted_directory_links", "tree_digest"]

MODULES = [f"src/mod{index}.py" for index in range(7)]
PAGES = [f"docs/page{index}.md" for index in range(7)]
EXECUTABLE = {"bin/run.sh", ".husky/pre-commit"}
SOURCE = {"src/app.py": "IMPLEMENTATION\n", "api/spec.txt": "PUBLIC_BEHAVIOR\n",
          # A regular file whose bytes are a link target: Git stores the same blob for the
          # link "api/pointer -> spec.txt", so only the kind of entry tells them apart.
          "api/pointer": "spec.txt",
          "bin/run.sh": "#!/bin/sh\nexit 0\n", ".husky/pre-commit": "npx lint-staged\n",
          "apps/website/package.json": "{}\n", ".gitignore": "node_modules/\n*.log\n",
          "tests/test_app.py": "ORACLE\n", ".kin/knowledge.jsonl": "PRIVATE_MEMORY\n",
          **{name: f"{name}\n" for name in MODULES + PAGES}}
# The Coder's projection of SOURCE: everything but tests/ and .kin/.
CODER_FILES = sorted([*(name for name in SOURCE if not name.startswith(("tests/", ".kin/"))),
                      "api/alias"])


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "--allow-empty", "-m", message)


def _source(repo):
    """The fixture source repository: SOURCE plus the file link api/alias -> spec.txt."""
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, value in SOURCE.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        path.chmod(0o755 if name in EXECUTABLE else 0o644)
    (repo / "api/alias").symlink_to("spec.txt")
    _commit(repo, "source")
    return repo


@pytest.fixture
def inputs(tmp_path):
    repo = _source(tmp_path / "source")
    runs = tmp_path / "runs"
    root = run_record.create(runs, "task", repo, b"Implement public behavior")
    return repo, runs, root


@pytest.fixture
def coder(tmp_path, inputs):
    """A prepared Coder projection that verifies before anything touches it."""
    _, runs, root = inputs
    dest = tmp_path / "coder"
    result = role_inputs.prepare(runs, "task", "coder", dest, [], ["tests"])
    assert result["files"] == CODER_FILES
    assert role_inputs.verify(runs, "task", "coder", dest) == result  # positive control
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


_CHANGED_MODES = []


@pytest.fixture(autouse=True)
def _restore_modes():
    """Restore the original mode of every path a test chmods, whether it passes or fails.

    An unlistable directory left behind stops pytest's basetemp rotation and `rm -rf`.
    """
    yield
    while _CHANGED_MODES:
        path, mode = _CHANGED_MODES.pop()  # last changed first: a parent before its children
        os.chmod(path, mode)


def _chmod(path, mode):
    """chmod a path a test changes, recording its mode so teardown can restore it."""
    _CHANGED_MODES.append((path, stat.S_IMODE(path.lstat().st_mode)))
    path.chmod(mode)


def _unreadable(path):
    if os.geteuid() == 0:
        pytest.skip("root reads files whatever their permission bits")
    _chmod(path, 0)
    with pytest.raises(PermissionError):  # positive control: it really cannot be read
        path.read_bytes()


def _unlistable(path):
    if os.geteuid() == 0:
        pytest.skip("root lists directories whatever their permission bits")
    _chmod(path, 0o300)  # writable and searchable, but not readable
    with pytest.raises(PermissionError):  # positive control: it really cannot be listed
        os.listdir(path)


# --- receipts and tree digests are what they were -------------------------------------------


def test_prepare_writes_exactly_the_receipt_main_writes_and_nothing_else(tmp_path, coder):
    dest, runs, root, result = coder
    assert list(result) == RECEIPT_KEYS  # no file_digests, no other new key
    assert json.loads((root / "inputs/coder.json").read_text()) == result
    tester = role_inputs.prepare(runs, "task", "tester", tmp_path / "tester", [], [])
    assert list(tester) == RECEIPT_KEYS and tester["files"] == []
    assert sorted(path.name for path in (root / "inputs").iterdir()) == [
        "coder.json", "tester.json"]
    assert (dest / MARKER).is_file() and (dest / MARKER).stat().st_size == 0
    assert MARKER not in _git(dest, "ls-tree", "-r", "--name-only", "HEAD").splitlines()


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


def test_tree_digest_pin(tmp_path):
    # Computed by tree_digest at ecddf63. Receipts already written depend on it.
    tree = _pinned_tree(tmp_path / "tree")
    assert role_inputs.tree_digest(tree) == (
        "b11045b9b68357e20ba61e57d18df14d0582be78c28a0d783c6ca640926e015e")
    _chmod(tree / "README.md", 0o664)  # the digest covers every permission bit
    assert role_inputs.tree_digest(tree) == (
        "2c8aae0cc6466d215a9a69c40d48756be6903e21237e5e362d368f049aebd2cd")


@pytest.mark.skipif(sys.platform != "linux", reason="a link's own mode is 0777 only on Linux")
def test_tree_digest_pin_with_links(tmp_path):
    # Computed by tree_digest at ecddf63, on Linux.
    tree = _pinned_tree(tmp_path / "tree")
    (tree / "nested/alias").symlink_to("deep/data.bin")
    (tree / "dangling").symlink_to("missing")
    assert role_inputs.tree_digest(tree) == (
        "d0374bbd4dbc49b0a926a2d876bacf50f3fad261a917a61f5444bc0cf6fc6ce1")
    (tree / "nested/alias").unlink()
    (tree / "nested/alias").symlink_to("../README.md")
    assert role_inputs.tree_digest(tree) == (
        "30ca58f5daee3d9879d1cfb4e0ae0e7b24516adeb321a29cbe177e177b58aa29")


def test_receipt_tree_digest_pin(tmp_path, inputs):
    # Computed by prepare at ecddf63 for the same source; the marker is part of the digest.
    _, runs, _ = inputs
    public = role_inputs.prepare(runs, "task", "tester", tmp_path / "tester", ["bin", "docs"], [])
    assert public["files"] == sorted(["bin/run.sh", *PAGES])
    assert public["tree_digest"] == (
        "688eaf4e081294c831118e56e0db6c250a12af70b6d6eadbeb3e9c8074fa58a0")
    assert role_inputs.tree_digest(tmp_path / "tester") == public["tree_digest"]
    assert role_inputs.verify(runs, "task", "tester", tmp_path / "tester") == public


def test_an_empty_projection_digests_only_the_marker(tmp_path, inputs):
    source, runs, _ = inputs
    run_record.create(runs, "empty", source, b"Test public behavior")
    empty = role_inputs.prepare(runs, "empty", "tester", tmp_path / "tester", [], [])
    assert empty["tree_digest"] == (
        "21fb0abfd6487e430d4ca1391d9b75108671739061b3616862b38712631f7424")
    assert role_inputs.verify(runs, "empty", "tester", tmp_path / "tester") == empty


# --- problems: directory links, non-regular files, unreadable files -------------------------


def test_every_directory_link_is_named_sorted_and_a_link_alone_gives_only_that(coder):
    dest, runs, _, _ = coder
    # Created out of order across several committed directories.
    for name in ("src/zz_link", "docs/b_link", "api/link", "bin/a_link"):
        _link_dir(dest / name, dest / "docs")
    assert _refusal(runs, dest) == LINKS + "api/link, bin/a_link, docs/b_link, src/zz_link"


def test_directory_links_are_named_in_plain_string_order(coder):
    # Inferred: "sorted" is plain string order (the earlier contract said so): "api.b" sorts
    # before "api/x" ('.' < '/'), though a walk or a path-parts sort names api/x first.
    dest, runs, _, _ = coder
    for name in ("api/x", "api.b"):
        _link_dir(dest / name, dest / "src")
    assert _refusal(runs, dest) == LINKS + "api.b, api/x"


@pytest.mark.parametrize("count,rest", [(5, ""), (6, " and 1 more"), (8, " and 3 more")])
def test_at_most_five_directory_links_are_named(coder, count, rest):
    # Inferred: the five-and-N-more rule applies to problems as to changes.
    dest, runs, _, _ = coder
    names = [f"docs/link{index}" for index in range(count)]
    for name in reversed(names):
        _link_dir(dest / name, "../src")
    assert _refusal(runs, dest) == LINKS + ", ".join(names[:5]) + rest


def test_fifos_and_sockets_are_named_as_non_regular_files(coder, monkeypatch):
    dest, runs, _, _ = coder
    os.mkfifo(dest / "zz_pipe")  # a root entry: walked before api/, so only sorting orders it
    os.mkfifo(dest / "api/pipe")
    monkeypatch.chdir(dest / "src")  # a relative bind keeps the socket path short
    with socket.socket(socket.AF_UNIX) as server:
        server.bind("sock")
    assert (dest / "src/sock").is_socket()
    assert _refusal(runs, dest) == ODD + "api/pipe, src/sock, zz_pipe"


@pytest.mark.parametrize("count,rest", [(5, ""), (6, " and 1 more"), (8, " and 3 more")])
def test_at_most_five_non_regular_files_are_named(coder, count, rest):
    dest, runs, _, _ = coder
    names = [f"docs/pipe{index}" for index in range(count)]
    for name in reversed(names):
        os.mkfifo(dest / name)
    assert _refusal(runs, dest) == ODD + ", ".join(names[:5]) + rest


def test_a_file_that_cannot_be_read_is_named(coder):
    # Inferred: an unreadable committed file whose executable bit is unchanged is named only
    # as a problem (it is not removed, and its contents cannot be compared).
    dest, runs, _, _ = coder
    _unreadable(dest / "src/app.py")
    assert _refusal(runs, dest) == UNREADABLE + "src/app.py"


def test_a_read_error_does_not_hide_the_other_problems_or_changes(coder):
    dest, runs, _, _ = coder
    _unreadable(dest / "src/mod3.py")
    _unreadable(dest / "api/spec.txt")
    _link_dir(dest / "src/zz_link", "../docs")
    os.mkfifo(dest / "docs/pipe")
    _write(dest / "leaked.py", "IMPLEMENTATION")
    assert _refusal(runs, dest) == (
        LINKS + "src/zz_link; a non-regular file: docs/pipe; "
        "a file that cannot be read: api/spec.txt, src/mod3.py" + BOTH + "added leaked.py")


@pytest.mark.parametrize("directories,named", [
    (["docs"], "docs/"),
    (["apps/website"], "apps/website/"),
    (["docs", "apps/website"], "apps/website/, docs/"),
])
def test_a_directory_that_cannot_be_listed_is_named_and_hides_no_removal(coder, directories,
                                                                         named):
    # Amended contract: an unlistable directory is a problem named "dir/"; the committed
    # files under it are not reported as removed.
    dest, runs, _, _ = coder
    for directory in directories:
        _unlistable(dest / directory)
    assert _refusal(runs, dest) == UNLISTABLE + named


def test_every_problem_kind_is_named_in_the_contract_order(coder):
    # Amended contract: directory symlink, non-regular file, file that cannot be read,
    # directory that cannot be read. Inferred: kinds are joined by "; ".
    dest, runs, _, _ = coder
    _unlistable(dest / "docs")
    _unreadable(dest / "src/mod3.py")
    os.mkfifo(dest / "api/pipe")
    _link_dir(dest / "src/zz_link", "../bin")
    _write(dest / "leaked.py", "IMPLEMENTATION")
    assert _refusal(runs, dest) == (
        LINKS + "src/zz_link; a non-regular file: api/pipe; "
        "a file that cannot be read: src/mod3.py; a directory that cannot be read: docs/"
        + BOTH + "added leaked.py")


@pytest.mark.parametrize("replacement,expected", [
    ("directory link", LINKS + "src/mod0.py"), ("fifo", ODD + "src/mod0.py")])
def test_a_committed_file_turned_link_or_fifo_is_not_also_removed(coder, replacement, expected):
    dest, runs, _, _ = coder
    (dest / "src/mod0.py").unlink()
    if replacement == "fifo":
        os.mkfifo(dest / "src/mod0.py")
    else:
        _link_dir(dest / "src/mod0.py", "../docs")
    assert _refusal(runs, dest) == expected


@pytest.mark.parametrize("directory", ["docs", "apps/website"])
def test_a_committed_directory_replaced_by_a_link_is_named_only_as_a_link(coder, directory):
    # Amended contract: the committed files under it are not reported as removed.
    dest, runs, _, _ = coder
    shutil.rmtree(dest / directory)
    _link_dir(dest / directory, dest / "src")
    assert _refusal(runs, dest) == LINKS + directory


@pytest.mark.parametrize("directory", ["docs", "apps/website"])
def test_a_committed_directory_replaced_by_a_fifo_is_named_only_as_that(coder, directory):
    # Amendment 4: the committed files under it are not reported as removed.
    dest, runs, _, _ = coder
    shutil.rmtree(dest / directory)
    os.mkfifo(dest / directory)
    assert _refusal(runs, dest) == ODD + directory


def test_a_new_directory_that_cannot_be_listed_is_not_also_added(coder):
    # Amendment 4: named only as a directory that cannot be read, not also "added new/".
    dest, runs, _, _ = coder
    _write(dest / "new/leaked.py", "IMPLEMENTATION")
    _unlistable(dest / "new")
    assert _refusal(runs, dest) == UNLISTABLE + "new/"


# --- the baseline: Git attributes, odd names, an unreadable first commit --------------------


@pytest.fixture
def normalized(tmp_path):
    """A Coder projection holding a CRLF file that `*.txt text` normalizes when committed.

    The source commits the CRLF bytes before .gitattributes exists, then adds .gitattributes
    without renormalizing; the projection's first commit stores the file with LF endings while
    the clone holds the CRLF bytes.
    """
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "core.autocrlf", "false")
    _write(repo / "src/app.py", "IMPLEMENTATION\n")
    (repo / "api").mkdir()
    (repo / "api/crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    _commit(repo, "crlf")
    (repo / ".gitattributes").write_text("*.txt text\n")
    _git(repo, "add", ".gitattributes")
    _git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "-m", "attributes")
    assert b"\r\n" in subprocess.run(["git", "-C", str(repo), "show", "HEAD:api/crlf.txt"],
                                     check=True, capture_output=True).stdout
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repo, b"Implement public behavior")
    dest = tmp_path / "coder"
    result = role_inputs.prepare(runs, "task", "coder", dest, [], [])
    # Positive control: the clone holds CRLF bytes; its first commit stores them normalized.
    assert (dest / "api/crlf.txt").read_bytes() == b"one\r\ntwo\r\n"
    assert subprocess.run(["git", "-C", str(dest), "show", "HEAD:api/crlf.txt"], check=True,
                          capture_output=True).stdout == b"one\ntwo\n"
    return dest, runs, result


@pytest.mark.parametrize("change,expected", [
    ("none", None),
    # Amendment 3: compared as `git add` would commit it now, the CRLF file is unchanged.
    ("link", LINKS + "src/zz_link"),
    ("content", CHANGES + "changed api/crlf.txt"),
    ("link+content", LINKS + "src/zz_link" + BOTH + "changed api/crlf.txt"),
    # LF bytes commit as the same blob, so nothing can be named; the tree digest differs.
    ("line endings", UNSHOWN),
])
def test_contents_are_compared_as_git_would_commit_them(normalized, change, expected):
    dest, runs, result = normalized
    if "link" in change:
        _link_dir(dest / "src/zz_link", "../api")
    if "content" in change:
        (dest / "api/crlf.txt").write_bytes(b"one\r\nTHREE\r\n")
    if change == "line endings":
        (dest / "api/crlf.txt").write_bytes(b"one\ntwo\n")
    if expected is None:
        assert role_inputs.verify(runs, "task", "coder", dest) == result
    else:
        assert _refusal(runs, dest) == expected


# From amendment 6: a leading '"' and a newline. Inferred: Git quotes a tab and non-ASCII
# bytes in its plain output the same way, so those are names of the same kind.
ODD_NAMES = ['"quoted.txt', "line\nbreak.txt", "tab\tand space.txt", "café.txt"]


@pytest.fixture
def odd(tmp_path):
    """A Coder projection whose committed names Git quotes or escapes in its plain output."""
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _write(repo / "src/app.py", "IMPLEMENTATION\n")
    for name in ODD_NAMES:
        _write(repo / "docs" / name, f"{name}\n")
    _commit(repo, "odd names")
    runs = tmp_path / "runs"
    run_record.create(runs, "task", repo, b"Implement public behavior")
    dest = tmp_path / "coder"
    result = role_inputs.prepare(runs, "task", "coder", dest, [], [])
    assert sorted(result["files"]) == sorted(["src/app.py", *(f"docs/{n}" for n in ODD_NAMES)])
    return dest, runs, result


@pytest.mark.parametrize("name", ODD_NAMES)
def test_a_committed_odd_name_verifies_and_is_named_when_edited(odd, name):
    # Amendment 6: unchanged passes; an edit names the path as it is.
    dest, runs, result = odd
    assert role_inputs.verify(runs, "task", "coder", dest) == result
    (dest / "docs" / name).write_text("CHANGED\n")
    assert _refusal(runs, dest) == CHANGES + "changed docs/" + name


def _break_first_commit(dest):
    """Delete the first commit's root tree object; HEAD still resolves to that commit."""
    tree = _git(dest, "rev-parse", "HEAD^{tree}")
    loose = dest / ".git/objects" / tree[:2] / tree[2:]
    assert loose.is_file()  # positive control: a loose object this test can remove
    loose.unlink()
    assert subprocess.run(["git", "-C", str(dest), "cat-file", "-e", tree],
                          capture_output=True).returncode != 0
    _git(dest, "rev-parse", "HEAD")  # still resolves


def test_an_unreadable_first_commit_is_said_to_be_unreadable(coder):
    # Amendment 5: with no problem, the refusal says no change can be named, and why.
    dest, runs, _, _ = coder
    _break_first_commit(dest)
    (dest / "src/app.py").write_text("CHANGED\n")
    message = _refusal(runs, dest)
    prefix = CHANGES + "none can be named: " + UNREAD_BASELINE
    assert message.startswith(prefix) and message.endswith(")"), message
    assert len(message) > len(prefix) + 1  # Git's error is carried


def test_an_unreadable_first_commit_still_names_the_problems(coder):
    # Amendment 5: with a problem, the problems are named and other changes cannot be.
    dest, runs, _, _ = coder
    _break_first_commit(dest)
    _link_dir(dest / "src/zz_link", "../docs")
    (dest / "src/app.py").write_text("CHANGED\n")
    message = _refusal(runs, dest)
    prefix = LINKS + "src/zz_link; other changes cannot be named: " + UNREAD_BASELINE
    assert message.startswith(prefix) and message.endswith(")"), message
    assert len(message) > len(prefix) + 1


# --- unrecorded changes ---------------------------------------------------------------------


def _change(dest, kinds):
    if "add" in kinds:
        # A root file is walked before api/, so only sorting puts api/new.txt first.
        _write(dest / "zz_new.txt", "new")
        _write(dest / "api/new.txt", "new")
    if "remove" in kinds:
        (dest / "src/mod1.py").unlink()
        (dest / "docs/page3.md").unlink()
    if "content" in kinds:
        (dest / "src/app.py").write_text("CHANGED\n")
    if "exec on" in kinds:
        _chmod(dest / "api/spec.txt", 0o755)
    if "exec off" in kinds:
        _chmod(dest / "bin/run.sh", 0o644)
    if "file to link" in kinds:
        (dest / "src/app.py").unlink()
        (dest / "src/app.py").symlink_to("mod0.py")
    if "file to same-bytes link" in kinds:
        pointer = dest / "api/pointer"
        pointer.unlink()
        pointer.symlink_to(SOURCE["api/pointer"])
        # Positive control: Git stores this link as the committed file's very blob, and the
        # link leads to an existing file.
        target = subprocess.run(["git", "-C", str(dest), "hash-object", "--stdin"],
                                input=os.readlink(pointer), check=True, capture_output=True,
                                text=True).stdout.strip()
        assert target == _git(dest, "rev-parse", "HEAD:api/pointer")
        assert pointer.is_symlink() and pointer.is_file()
    if "link to file" in kinds:
        # The same bytes Git stores for the link: only the kind of entry differs.
        (dest / "api/alias").unlink()
        (dest / "api/alias").write_bytes(b"spec.txt")
    if "link target" in kinds:
        (dest / "api/alias").unlink()
        (dest / "api/alias").symlink_to("../src/app.py")


@pytest.mark.parametrize("change,expected", [
    ("add", "added api/new.txt, zz_new.txt"),
    ("remove", "removed docs/page3.md, src/mod1.py"),
    ("content", "changed src/app.py"),
    ("exec on", "changed api/spec.txt"),
    ("exec off", "changed bin/run.sh"),
    ("file to link", "changed src/app.py"),
    ("file to same-bytes link", "changed api/pointer"),
    ("link to file", "changed api/alias"),
    ("link target", "changed api/alias"),
    ("add+content", "added api/new.txt, zz_new.txt; changed src/app.py"),
    ("remove+exec on", "removed docs/page3.md, src/mod1.py; changed api/spec.txt"),
    ("add+remove", "added api/new.txt, zz_new.txt; removed docs/page3.md, src/mod1.py"),
    ("add+remove+content+exec on+exec off+link target",
     "added api/new.txt, zz_new.txt; removed docs/page3.md, src/mod1.py; "
     "changed api/alias, api/spec.txt, bin/run.sh, src/app.py"),
])
def test_each_change_is_named_in_its_kind_in_order(coder, change, expected):
    dest, runs, _, _ = coder
    _change(dest, change.split("+"))
    assert _refusal(runs, dest) == CHANGES + expected


def test_each_kind_names_at_most_five_paths(coder):
    dest, runs, _, _ = coder
    added = [f"src/new{index}.py" for index in range(6)]
    for name in reversed(added):
        _write(dest / name, "new")
    for name in PAGES:
        (dest / name).unlink()
    for name in MODULES:
        (dest / name).write_text("CHANGED\n")
    assert _refusal(runs, dest) == CHANGES + (
        "added " + ", ".join(added[:5]) + " and 1 more; "
        "removed " + ", ".join(PAGES[:5]) + " and 2 more; "
        "changed " + ", ".join(MODULES[:5]) + " and 2 more")


@pytest.mark.parametrize("files,expected", [
    # A new top-level directory, its nested folders and file links included, is named once.
    (["node_modules/pkg/index.js", "node_modules/pkg/lib/a.js", "node_modules/.bin/tool"],
     "added node_modules/"),
    # A new folder inside a committed one (husky's .husky/_ beside .husky/pre-commit).
    ([".husky/_/.gitignore", ".husky/_/h"], "added .husky/_/"),
    (["src/newpkg/x.py", "src/newpkg/sub/y.py", "src/zz.py", "extra/f"],
     "added extra/, src/newpkg/, src/zz.py"),
])
def test_a_directory_the_first_commit_never_had_is_named_once(coder, files, expected):
    dest, runs, _, _ = coder
    for name in files:
        if name.endswith("/tool"):
            _write(dest / "node_modules/pkg/cli.js", "cli")
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            (dest / name).symlink_to("../pkg/cli.js")
        else:
            _write(dest / name, "new")
    assert _refusal(runs, dest) == CHANGES + expected


def test_new_directories_count_once_toward_the_five(coder):
    # Inferred: a directory named once counts once.
    dest, runs, _, _ = coder
    for index in range(6):
        _write(dest / f"d{index}/inner/f", "new")
    assert _refusal(runs, dest) == CHANGES + "added d0/, d1/, d2/, d3/, d4/ and 1 more"


@pytest.mark.parametrize("state,expected", [
    ("ignored by the committed .gitignore", "added debug.log, node_modules/"),
    ("ignored by .git/info/exclude", "added secret.py"),
    ("staged new file", "added leaked.py"),
    ("staged content", "changed src/app.py"),
    ("assume-unchanged", "changed src/app.py"),
    ("skip-worktree deletion", "removed src/mod1.py"),
    ("core.fileMode false", "changed api/spec.txt"),
])
def test_git_state_in_the_clone_cannot_hide_a_change(coder, state, expected):
    # The comparison is against the projection's first commit, not Git's view of the clone.
    dest, runs, _, _ = coder
    if state.startswith("ignored by the committed"):
        _write(dest / "node_modules/pkg/index.js", "module")
        _write(dest / "debug.log", "log")
    elif state.startswith("ignored by .git"):
        with (dest / ".git/info/exclude").open("a") as stream:
            stream.write("secret.py\n")
        _write(dest / "secret.py", "IMPLEMENTATION")
    elif state == "staged new file":
        _write(dest / "leaked.py", "IMPLEMENTATION")
        _git(dest, "add", "leaked.py")
    elif state == "staged content":
        (dest / "src/app.py").write_text("CHANGED\n")
        _git(dest, "add", "src/app.py")
    elif state == "assume-unchanged":
        _git(dest, "update-index", "--assume-unchanged", "src/app.py")
        (dest / "src/app.py").write_text("CHANGED\n")
    elif state == "skip-worktree deletion":
        _git(dest, "update-index", "--skip-worktree", "src/mod1.py")
        (dest / "src/mod1.py").unlink()
    else:
        _git(dest, "config", "core.fileMode", "false")
        _chmod(dest / "api/spec.txt", 0o755)
    assert _refusal(runs, dest) == CHANGES + expected


@pytest.mark.parametrize("change,expected", [
    ("delete", CHANGES + "removed " + MARKER),
    ("rewrite", CHANGES + "changed " + MARKER),
    ("file link", CHANGES + "changed " + MARKER),
    # Neither non-empty nor irregular: only its mode differs, which no commit records.
    ("chmod", UNSHOWN),
    ("delete+remove", CHANGES + "removed " + MARKER + ", src/mod1.py"),
])
def test_the_no_index_marker_is_named(coder, change, expected):
    # `git clean -fdx` in a lane clone deletes the marker: it is listed in .git/info/exclude.
    dest, runs, _, _ = coder
    marker = dest / MARKER
    if "delete" in change or change == "file link":
        marker.unlink()
    if change == "rewrite":
        marker.write_text("rewritten\n")
    if change == "file link":
        marker.symlink_to("api/spec.txt")
    if change == "chmod":
        _chmod(marker, 0o644)
    if "remove" in change:
        (dest / "src/mod1.py").unlink()
    assert _refusal(runs, dest) == expected


def test_prepare_writes_files_at_exactly_0644_or_git_executable_0755(tmp_path, inputs):
    # Amended contract: whatever the umask, so any other mode is a change it can name.
    _, runs, _ = inputs
    dest = tmp_path / "coder"
    previous = os.umask(0o077)
    try:
        result = role_inputs.prepare(runs, "task", "coder", dest, [], ["tests"])
    finally:
        os.umask(previous)
    executable = {line.split("\t", 1)[1] for line
                  in _git(dest, "ls-tree", "-r", "HEAD").splitlines()
                  if line.startswith("100755 ")}
    assert executable == EXECUTABLE  # positive control: Git records the executable files
    for name in result["files"]:
        info = (dest / name).lstat()
        if stat.S_ISREG(info.st_mode):
            assert stat.S_IMODE(info.st_mode) == (0o755 if name in EXECUTABLE else 0o644), name
    assert role_inputs.verify(runs, "task", "coder", dest) == result


@pytest.mark.parametrize("path,mode", [
    # Amended contract: any mode change of a committed regular file is named, whichever bit.
    ("api/spec.txt", 0o664), ("api/spec.txt", 0o600), ("api/spec.txt", 0o645),
    ("api/spec.txt", 0o655), ("api/spec.txt", 0o744), ("api/spec.txt", 0o444),
    ("bin/run.sh", 0o775), ("bin/run.sh", 0o700), ("bin/run.sh", 0o655),
    ("bin/run.sh", 0o744), ("bin/run.sh", 0o754), ("bin/run.sh", 0o644),
])
def test_any_mode_change_of_a_committed_file_is_named(coder, path, mode):
    dest, runs, _, _ = coder
    _chmod(dest / path, mode)
    assert _refusal(runs, dest) == CHANGES + "changed " + path


def test_a_mode_change_is_named_beside_other_changes_and_problems(coder):
    dest, runs, _, _ = coder
    _chmod(dest / "api/spec.txt", 0o664)
    (dest / "src/app.py").write_text("CHANGED\n")
    assert _refusal(runs, dest) == CHANGES + "changed api/spec.txt, src/app.py"
    (dest / "src/app.py").write_text(SOURCE["src/app.py"])
    _link_dir(dest / "src/zz_link", "../docs")
    assert _refusal(runs, dest) == LINKS + "src/zz_link" + BOTH + "changed api/spec.txt"


@pytest.mark.parametrize("made,expected", [
    # Amendment 7: a new directory holding nothing, or only directories, is not named.
    (["build/"], None),
    (["build/x/y/"], None),
    (["build/x/"], CHANGES + "changed api/spec.txt"),
    (["build/x/", "extra/f"], CHANGES + "added extra/; changed api/spec.txt"),
    # Whatever it holds deeper down counts: a FIFO, or a directory link.
    (["deep/x/pipe|"], ODD + "deep/x/pipe" + BOTH + "added deep/"),
    (["deep/x/link@"], LINKS + "deep/x/link" + BOTH + "added deep/"),
])
def test_a_new_directory_is_named_only_if_it_holds_more_than_directories(coder, made,
                                                                          expected):
    dest, runs, _, result = coder
    for name in made:
        path = dest / name.rstrip("/|@")
        if name.endswith("/"):
            path.mkdir(parents=True)
        elif name.endswith("|"):
            path.parent.mkdir(parents=True)
            os.mkfifo(path)
        elif name.endswith("@"):
            _link_dir(path, "../../src")
        else:
            _write(path, "new")
    if expected is None:
        assert role_inputs.verify(runs, "task", "coder", dest) == result
        return
    if "changed api/spec.txt" in expected:
        _chmod(dest / "api/spec.txt", 0o664)
    assert _refusal(runs, dest) == expected


def test_problems_and_changes_are_named_in_one_refusal(coder):
    dest, runs, _, _ = coder
    _link_dir(dest / "src/zz_link", "../docs")
    os.mkfifo(dest / "api/pipe")
    _change(dest, ["add", "remove", "content"])
    (dest / MARKER).unlink()
    assert _refusal(runs, dest) == (
        LINKS + "src/zz_link; a non-regular file: api/pipe" + BOTH
        + "added api/new.txt, zz_new.txt; removed .kindex-no-index, docs/page3.md, src/mod1.py; "
        "changed src/app.py")


# --- unchanged: HEAD and replacement rounds --------------------------------------------------


HEAD_MOVED = re.compile(r"role inputs changed before first launch: "
                        r"HEAD is ([0-9a-f]{7,40}), not the projection's ([0-9a-f]{7,40})")


def _head_refusal(runs, dest, initial):
    match = HEAD_MOVED.fullmatch(_refusal(runs, dest))
    assert match, "not the moved-HEAD refusal"
    assert _git(dest, "rev-parse", "HEAD").startswith(match[1])
    assert initial.startswith(match[2])


def test_a_moved_head_still_names_both_commits(coder):
    dest, runs, _, result = coder
    _commit(dest, "lane commit")
    _head_refusal(runs, dest, result["initial_commit"])
    # Inferred: HEAD is compared first, so a dirty tree on a moved HEAD gives the same refusal.
    _link_dir(dest / "src/zz_link", "../docs")
    (dest / "src/app.py").write_text("CHANGED\n")
    _head_refusal(runs, dest, result["initial_commit"])


def _record_launch(root, role, repository):
    lanes = root / "tmux-lanes"
    lanes.mkdir(exist_ok=True)
    row = {"schema_version": "factory-tmux-lane/1", "run_id": "task", "role": role, "slot": role,
           "lane": role, "repository": str(repository), "status": "active"}
    with (lanes / f"{role}-launch.jsonl").open("a") as journal:
        journal.write(json.dumps(row) + "\n")


def test_a_replacement_round_does_not_compare_the_tree(coder):
    dest, runs, root, result = coder
    _commit(dest, "lane work")
    _link_dir(dest / "node_modules/react", "../src")
    _write(dest / ".husky/_/h", "hook")
    with pytest.raises(ValueError, match="no recorded launch"):
        role_inputs.verify(runs, "task", "coder", dest, replacing=True)
    _record_launch(root, "coder", dest)
    assert role_inputs.verify(runs, "task", "coder", dest, replacing=True) == result


# --- a receipt and clone written by the code before this change -----------------------------


def test_a_projection_shaped_by_the_earlier_prepare_verifies_and_names_changes(coder):
    """Nothing new is stored, so a clone and receipt as ecddf63 made them work the same."""
    dest, runs, root, result = coder
    # Rebuild the clone's Git state exactly as ecddf63's prepare did, so nothing the current
    # prepare might keep in .git is there, and write the receipt with its keys alone.
    shutil.rmtree(dest / ".git")
    _git(dest, "init", "-q", "-b", "lane/coder")
    with (dest / ".git/info/exclude").open("a", encoding="utf-8") as stream:
        stream.write(f"\n/{MARKER}\n")
    _git(dest, "add", "-f", "--", ".", f":(top,exclude){MARKER}")
    _git(dest, "-c", "user.name=Factory", "-c", "user.email=factory@local", "commit",
         "--allow-empty", "-qm", "coder input projection")
    receipt = {key: result[key] for key in RECEIPT_KEYS}
    receipt["initial_commit"] = _git(dest, "rev-parse", "HEAD")
    (root / "inputs/coder.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert role_inputs.verify(runs, "task", "coder", dest) == receipt
    _link_dir(dest / "apps/website/node_modules/react", "../../../src")
    _write(dest / ".husky/_/h", "hook")
    _change(dest, ["remove", "content"])
    (dest / MARKER).unlink()
    assert _refusal(runs, dest) == (
        LINKS + "apps/website/node_modules/react" + BOTH
        + "added .husky/_/, apps/website/node_modules/; "
        "removed .kindex-no-index, docs/page3.md, src/mod1.py; changed src/app.py")


# --- the incident ----------------------------------------------------------------------------


INCIDENT = (LINKS + "apps/website/node_modules/react" + BOTH
            + "added .husky/_/, apps/website/node_modules/")


def _incident(dest):
    """What the 2026-11-17 install left: a package link and husky's generated folder."""
    _link_dir(dest / "apps/website/node_modules/react", "../../../src")
    _write(dest / ".husky/_/.gitignore", "*\n")
    _write(dest / ".husky/_/h", "#!/usr/bin/env sh\n")


def _named(message):
    """Every path a refusal names, from its "<problem>: a, b" and "<change kind> a, b" parts."""
    names = []
    for prefix in (CONTAINS, CHANGES):
        message = message.removeprefix(prefix)
    for part in re.split(r"; and unrecorded changes: |; ", message):
        listed = part.split(": ", 1)[1] if ": " in part else part.split(" ", 1)[1]
        assert not re.search(r" and \d+ more$", listed), "every path must be named to remove it"
        names += listed.split(", ")
    return names


def test_the_incident_is_refused_once_and_passes_after_removing_what_is_named(coder):
    dest, runs, _, result = coder
    _incident(dest)
    message = _refusal(runs, dest)
    assert message == INCIDENT
    # The operator removes exactly the named paths and nothing else.
    for name in _named(message):
        path = dest / name.rstrip("/")
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    assert role_inputs.verify(runs, "task", "coder", dest) == result


# --- what the operator sees -----------------------------------------------------------------


def _environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    env.update(HOME=str(home), PYTHONPATH=str(REPO), FACTORY_PYTHON=sys.executable)
    return env


def test_verify_command_exits_70_with_the_named_paths(tmp_path, coder):
    dest, runs, _, result = coder
    command = [sys.executable, str(REPO / "harness/role_inputs.py"), "verify", "--runs",
               str(runs), "--run", "task", "--role", "coder", "--repo", str(dest)]
    clean = subprocess.run(command, env=_environment(tmp_path), capture_output=True, text=True)
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout) == result
    _incident(dest)
    refused = subprocess.run(command, env=_environment(tmp_path), capture_output=True,
                             text=True)
    assert refused.returncode == 70
    assert "role inputs: " + INCIDENT in refused.stderr.splitlines()


def test_a_refused_launch_records_and_shows_the_named_paths(tmp_path, coder):
    dest, runs, root, _ = coder
    _incident(dest)
    brief = tmp_path / "brief"
    brief.write_text("Implement behavior")
    result = subprocess.run(["bash", str(REPO / "harness/tmux_lane.sh"), "task", "coder",
                             "launch", "--runs", str(runs), "--repo", str(dest), "--prompt",
                             str(brief)], env=_environment(tmp_path), capture_output=True,
                            text=True)
    assert result.returncode != 0
    assert INCIDENT in result.stderr
    assert "prepared role projection" in result.stderr
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    [refusal] = [row for row in events if row.get("kind") == "refusal-tmux-lane"]
    assert INCIDENT in str(refusal["detail"])
    assert refusal["exit_code"] == result.returncode
