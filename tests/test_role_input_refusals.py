"""A refused author launch names every path that caused the refusal, in one message.

Cites the operator incident (2026-11-17): an install left a directory link and husky's
`.husky/_/` in a lane clone, and two refusal rounds named neither. The receipt keeps main's
keys and tree digests. A refusal names directory links, non-regular files, unreadable files
and unlistable directories, then added, removed and changed paths (sorted, five per kind,
then " and N more"; a new folder once, as `dir/`). Changes are compared as raw bytes and
exact modes with the receipt's source commit, never the clone's own Git state, and nothing
the clone configures is run. Digest literals were computed once by the module at ecddf63.
"""

import builtins
import errno
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
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
INCIDENT = (LINKS + "apps/website/node_modules/react" + BOTH
            + "added .husky/_/, apps/website/node_modules/")
RECEIPT_KEYS = ["role", "repository", "source_commit", "initial_commit", "includes", "excludes",
                "files", "omitted_directory_links", "tree_digest"]  # as written at ecddf63
ODD_NAMES = ['"quoted.txt', "line\nbreak.txt", "trailing\r", "tab\tname.txt", "café.txt"]
EXECUTABLE = {"bin/run.sh", ".husky/pre-commit"}
# Committed before `*.txt text` was, so the CRLF bytes stay in the source commit.
SOURCE = {"src/app.py": b"IMPLEMENTATION\n", "api/spec.txt": b"PUBLIC_BEHAVIOR\n",
          "api/pointer": b"spec.txt", "api/crlf.txt": b"one\r\ntwo\r\n", "api/lf.txt": b"1\n2\n",
          "bin/run.sh": b"#!/bin/sh\nexit 0\n", ".husky/pre-commit": b"npx lint-staged\n",
          "apps/website/package.json": b"{}\n", ".gitignore": b"node_modules/\n",
          "tests/test_app.py": b"ORACLE\n", ".kin/knowledge.jsonl": b"PRIVATE_MEMORY\n",
          **{name: name.encode() + b"\n" for name in [
              *(f"src/mod{index}.py" for index in range(7)),
              *(f"docs/page{index}.md" for index in range(7))]},
          **{name: name.encode() + b"\r\n" for name in ODD_NAMES}}
SOURCE_LINKS = {"api/alias": "spec.txt"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo, message, *paths):
    _git(repo, "add", *(paths or ["-A"]))
    _git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-q", "--allow-empty", "-m", message)


def _clone(tmp_path, files, links, excludes=("tests",)):
    """A source commit of files and links, its run, and a Coder clone prepared under umask 077."""
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "core.autocrlf", "false")
    for name, data in files.items():
        _write(source / name, data)
        (source / name).chmod(0o755 if name in EXECUTABLE else 0o644)
    for name, target in links.items():
        (source / name).symlink_to(target)
    _commit(source, "files")
    files = {**files, ".gitattributes": b"*.txt text\n"}  # added later, not renormalizing
    _write(source / ".gitattributes", files[".gitattributes"])
    _commit(source, "attributes", ".gitattributes")
    runs, dest = tmp_path / "runs", tmp_path / "coder"
    root = run_record.create(runs, "task", source, b"Implement public behavior")
    umask = os.umask(0o077)
    try:
        result = role_inputs.prepare(runs, "task", "coder", dest, [], list(excludes))
    finally:
        os.umask(umask)
    assert result["files"] == sorted(name for name in [*files, *links]
                                     if not name.startswith(("tests/", ".kin/")))
    for name in set(result["files"]) - set(links):  # raw source bytes, exactly 0644 or 0755
        assert (dest / name).read_bytes() == files[name]
        assert stat.S_IMODE((dest / name).stat().st_mode) == (
            0o755 if name in EXECUTABLE else 0o644)
    assert role_inputs.verify(runs, "task", "coder", dest) == result  # positive control
    return dest, runs, root, result, source


@pytest.fixture
def coder(tmp_path):
    return _clone(tmp_path, SOURCE, SOURCE_LINKS)


def _refusal(runs, dest, **kwargs):
    with pytest.raises(ValueError) as refused:
        role_inputs.verify(runs, "task", "coder", dest, **kwargs)
    return str(refused.value)


def _link_dir(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target, target_is_directory=True)
    assert path.is_dir()  # a directory link, not a file link


def _write(path, data=b"new"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


_CHANGED_MODES = []


@pytest.fixture(autouse=True)
def _restore_modes():  # so pytest and `rm -rf` can remove what a test denied
    yield
    while _CHANGED_MODES:
        path, mode = _CHANGED_MODES.pop()
        os.chmod(path, mode)


def _chmod(path, mode):
    _CHANGED_MODES.append((path, stat.S_IMODE(path.lstat().st_mode)))
    path.chmod(mode)


def _do(dest, steps):
    """Apply (operation, path, argument...) steps to the clone."""
    for op, name, *arg in steps:
        path = dest / name
        if op == "link":
            _link_dir(path, arg[0])
        elif op == "fifo":
            os.mkfifo(path)
        elif op == "write":
            _write(path, *arg)
        elif op == "rm":
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        elif op == "relink":  # a file link in place of what was there
            path.unlink()
            path.symlink_to(arg[0])
        elif op == "mkdir":
            path.mkdir(parents=True)
        elif op == "chmod":
            _chmod(path, arg[0])
        elif op == "deny":
            if os.geteuid() == 0:
                pytest.skip("root reads and lists whatever the mode")
            _chmod(path, arg[0])
            with pytest.raises(PermissionError):  # positive control
                os.listdir(path) if path.is_dir() else path.read_bytes()
        elif op == "git":
            _git(dest, name, *arg)
        elif op == "break first commit":
            tree = _git(dest, "rev-parse", "HEAD^{tree}")
            (dest / ".git/objects" / tree[:2] / tree[2:]).unlink()


EDIT, ADD, REMOVE = ("write", "src/app.py", b"CHANGED\n"), ("write", "leaked.py"), \
    ("rm", "src/mod1.py")
FIVE = [f"src/new{index}.py" for index in range(6)]
PAGES = [f"docs/page{index}.md" for index in range(7)]
CASES = {
    "incident": ([("link", "apps/website/node_modules/react", "../../../src"),
                  ("write", ".husky/_/h")], INCIDENT),
    "every problem kind in order, links in plain string order; replaced files not removed": (
        [ADD, ("rm", "api/pointer"), ("link", "api/pointer", "../src"), ("link", "api.b", "src"),
         ("rm", "src/mod5.py"), ("fifo", "src/mod5.py"), ("deny", "src/mod3.py", 0),
         ("deny", "docs", 0o300)],
        LINKS + "api.b, api/pointer; a non-regular file: src/mod5.py; a file that cannot be "
        "read: src/mod3.py; a directory that cannot be read: docs/" + BOTH + "added leaked.py"),
    "folder replaced by a link is not removed": ([("rm", "docs"), ("link", "docs", "src")],
                                                 LINKS + "docs"),
    "folder replaced by a fifo is not removed": ([("rm", "docs"), ("fifo", "docs")], ODD + "docs"),
    "file replaced by an unlistable folder": (
        [("rm", "src/mod0.py"), ("write", "src/mod0.py/x"), ("deny", "src/mod0.py", 0), ADD,
         REMOVE, EDIT],
        UNLISTABLE + "src/mod0.py/" + BOTH + "added leaked.py; removed src/mod1.py; "
        "changed src/app.py"),
    "new unlistable folder is not also added": ([("write", "new/x"), ("deny", "new", 0o300)],
                                                UNLISTABLE + "new/"),
    "new folder holding an unlistable one": (
        [("write", "new/sub/x"), ("deny", "new/sub", 0o300)],
        UNLISTABLE + "new/sub/" + BOTH + "added new/"),
    "kinds in order, each sorted": (
        [("write", "zz_new.txt"), ("write", "api/new.txt"), REMOVE, ("rm", "docs/page3.md"),
         EDIT, ("chmod", "bin/run.sh", 0o644), ("relink", "api/alias", "../src/app.py")],
        CHANGES + "added api/new.txt, zz_new.txt; removed docs/page3.md, src/mod1.py; "
        "changed api/alias, bin/run.sh, src/app.py"),
    "five per kind": ([("write", name) for name in FIVE] + [("rm", name) for name in PAGES],
                      CHANGES + "added " + ", ".join(FIVE[:5]) + " and 1 more; removed "
                      + ", ".join(PAGES[:5]) + " and 2 more"),
    # Git stores the same blob for a file and a link with these bytes.
    "file to a link of the same bytes": ([("relink", "api/pointer", "spec.txt")],
                                         CHANGES + "changed api/pointer"),
    "link to a file of the same bytes": (
        [("rm", "api/alias"), ("write", "api/alias", b"spec.txt")], CHANGES + "changed api/alias"),
    "any mode change, beside a problem": (
        [("chmod", "api/spec.txt", 0o664), ("link", "src/zz", "../docs")],
        LINKS + "src/zz" + BOTH + "changed api/spec.txt"),
    "line endings are content": (
        [("write", "api/crlf.txt", b"one\ntwo\n"), ("write", "api/lf.txt", b"1\r\n2\r\n")],
        CHANGES + "changed api/crlf.txt, api/lf.txt"),
    "attributes are only a file": (
        [("write", ".gitattributes", b"* text=auto eol=crlf\n"), ("link", "src/zz", "../api")],
        LINKS + "src/zz" + BOTH + "changed .gitattributes"),
    "odd names, as they are": ([("write", name, b"CHANGED\r\n") for name in ODD_NAMES],
                               CHANGES + "changed " + ", ".join(sorted(ODD_NAMES))),
    "a new folder of folders is not named": (
        [("mkdir", "build/x/y"), ("chmod", "api/spec.txt", 0o664)],
        CHANGES + "changed api/spec.txt"),
    "the clone's Git state hides nothing": (
        [("write", ".git/info/exclude", b"secret.py\n"), ("write", "secret.py"),
         ("git", "update-index", "--assume-unchanged", "src/app.py"), EDIT, ADD,
         ("git", "add", "leaked.py"), ("git", "config", "core.fileMode", "false"),
         ("chmod", "api/spec.txt", 0o755)],
        CHANGES + "added leaked.py, secret.py; changed api/spec.txt, src/app.py"),
    "the clone's first commit is not the baseline": ([("break first commit", ""), EDIT],
                                                     CHANGES + "changed src/app.py"),
    "marker rewritten with content": ([("write", MARKER, b"x")], CHANGES + "changed " + MARKER),
    "marker mode, beside a change": ([("chmod", MARKER, 0o644), EDIT],
                                     CHANGES + "changed " + MARKER + ", src/app.py"),
}


@pytest.mark.parametrize("steps,expected", CASES.values(), ids=list(CASES))
def test_one_refusal_names_every_cause(coder, steps, expected):
    dest, runs, *_ = coder
    _do(dest, steps)
    assert _refusal(runs, dest) == expected


def test_a_removed_marker_is_restored_not_refused(coder):
    # Seen in a run: `git clean -fdx` undoing a pre-launch install removed the marker too.
    dest, runs, _, result, _ = coder
    (dest / MARKER).unlink()
    assert role_inputs.verify(runs, "task", "coder", dest) == result
    info = (dest / MARKER).lstat()
    assert stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 0


def test_the_receipt_and_tree_digest_are_mains(tmp_path, coder):
    dest, runs, root, result, _ = coder
    assert list(result) == RECEIPT_KEYS
    assert json.loads((root / "inputs/coder.json").read_text()) == result
    public = role_inputs.prepare(runs, "task", "tester", tmp_path / "tester", ["bin", "docs"], [])
    assert public["tree_digest"] == (
        "688eaf4e081294c831118e56e0db6c250a12af70b6d6eadbeb3e9c8074fa58a0")
    assert sorted(path.name for path in (root / "inputs").iterdir()) == [
        "coder.json", "tester.json"]


def _cli_and_launch(tmp_path, runs, dest):
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(REPO), FACTORY_PYTHON=sys.executable)

    def run(*command):
        return subprocess.run(command, env=env, capture_output=True, text=True)

    (tmp_path / "brief").write_text("Implement behavior")
    return (run(sys.executable, str(REPO / "harness/role_inputs.py"), "verify", "--runs",
                str(runs), "--run", "task", "--role", "coder", "--repo", str(dest)),
            run("bash", str(REPO / "harness/tmux_lane.sh"), "task", "coder", "launch", "--runs",
                str(runs), "--repo", str(dest), "--prompt", str(tmp_path / "brief")))


def test_the_incident_is_refused_once_shown_and_cleared(tmp_path, coder):
    dest, runs, root, result, _ = coder
    _do(dest, CASES["incident"][0])
    cli, launch = _cli_and_launch(tmp_path, runs, dest)
    assert cli.returncode == 70 and "role inputs: " + INCIDENT in cli.stderr.splitlines()
    assert launch.returncode != 0 and INCIDENT in launch.stderr
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    [refusal] = [row for row in events if row.get("kind") == "refusal-tmux-lane"]
    assert INCIDENT in refusal["detail"] and refusal["exit_code"] == launch.returncode
    for named in ("apps/website/node_modules", ".husky/_"):  # what the refusal named
        shutil.rmtree(dest / named)
    assert role_inputs.verify(runs, "task", "coder", dest) == result


def test_a_refusal_runs_nothing_the_clone_configures(tmp_path, coder):
    # Codex review of #122: hashing with the clone's attributes ran its filter.*.clean.
    dest, runs, _, _, _ = coder
    ran, program, hooks = tmp_path / "ran", tmp_path / "planted.sh", tmp_path / "hooks"
    for folder in (ran, hooks):
        folder.mkdir()
    program.write_text(f'#!/bin/sh\n: > "{ran}/$1"\n'
                       'case "$1" in clean|smudge) exec cat ;; textconv) exec cat "$2" ;; esac\n')
    for hook in ("pre-commit", "post-checkout", "post-index-change", "reference-transaction"):
        (hooks / hook).write_text(f'#!/bin/sh\nexec "{program}" {hook}\n')
        (hooks / hook).chmod(0o755)
    program.chmod(0o755)
    for key, value in {"filter.plant.clean": "clean", "filter.plant.smudge": "smudge",
                       "diff.plant.textconv": "textconv", "diff.external": "external-diff",
                       "core.fsmonitor": "fsmonitor"}.items():
        _git(dest, "config", key, f"{program} {value}")
    _git(dest, "config", "core.hooksPath", str(hooks))
    (dest / ".git/info/attributes").write_text("* filter=plant diff=plant\n")
    _do(dest, [EDIT])
    # Positive control: plain Git in the clone runs each plant (fsmonitor would hide the diff).
    for args in (["hash-object", "src/app.py"], ["-c", "core.fsmonitor=false", "diff"],
                 ["-c", "core.fsmonitor=false", "diff", "--no-ext-diff"], ["status"],
                 ["hook", "run", "pre-commit"]):
        subprocess.run(["git", "-C", str(dest), *args], capture_output=True)
    _git(dest, "config", "filter.plant.process", f"{program} process")  # it would abort a diff
    subprocess.run(["git", "-C", str(dest), "hash-object", "src/app.py"], capture_output=True)
    assert {path.name for path in ran.iterdir()} >= {
        "clean", "external-diff", "textconv", "fsmonitor", "pre-commit", "process"}
    for path in ran.iterdir():
        path.unlink()
    assert _refusal(runs, dest) == CHANGES + "changed src/app.py"
    cli, launch = _cli_and_launch(tmp_path, runs, dest)
    assert cli.returncode == 70 and launch.returncode != 0
    assert list(ran.iterdir()) == []


def _fail_reads(monkeypatch, path):
    """Python-level reads of path's file fail with EIO though it opens; matched by inode."""
    inode = (path.stat().st_dev, path.stat().st_ino)

    def ours(fd):
        try:
            info = os.fstat(fd)
        except (OSError, TypeError, ValueError):
            return False
        return (info.st_dev, info.st_ino) == inode

    def eio(*_args, **_kwargs):
        raise OSError(errno.EIO, os.strerror(errno.EIO), str(path))

    class Failing:
        __iter__ = __next__ = read = read1 = readall = readinto = readinto1 = eio
        readline = readlines = eio

        def __init__(self, stream):
            self._stream = stream

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self._stream.close()

    def wrap(stream):
        try:
            return Failing(stream) if ours(stream.fileno()) else stream
        except (OSError, ValueError, AttributeError):
            return stream

    for owner in (builtins, io, pathlib.Path):
        real = owner.open
        monkeypatch.setattr(owner, "open", lambda *a, _real=real, **k: wrap(_real(*a, **k)))
    for name in ("read", "pread", "readv", "preadv"):
        if hasattr(os, name):
            real = getattr(os, name)
            monkeypatch.setattr(os, name, lambda fd, *a, _real=real: (
                eio() if ours(fd) else _real(fd, *a)))
    for read in (path.read_bytes, lambda: os.read(os.open(path, os.O_RDONLY), 1)):
        with pytest.raises(OSError):  # positive control
            read()


@pytest.mark.parametrize("steps,expected", [
    ([], UNREADABLE + "src/mod3.py"),
    ([("link", "zz", "src"), ADD, REMOVE, EDIT],
     LINKS + "zz; a file that cannot be read: src/mod3.py" + BOTH
     + "added leaked.py; removed src/mod1.py; changed src/app.py"),
])
def test_a_read_error_is_named_and_never_escapes(coder, monkeypatch, steps, expected):
    # Round-4 review: a file that opens but cannot be read (EIO).
    dest, runs, _, _, _ = coder
    _do(dest, steps)
    _fail_reads(monkeypatch, dest / "src/mod3.py")
    assert _refusal(runs, dest) == expected


UNREAD = "the source commit could not be read ("


@pytest.mark.parametrize("breaks,steps,expected", [
    ("not a repository", [EDIT], CHANGES + "none can be named: " + UNREAD),
    ("commit object missing", [ADD, REMOVE, ("link", "src/zz", "../docs"), EDIT],
     LINKS + "src/zz" + BOTH + "added leaked.py; removed src/mod1.py; "
     "other changes cannot be named: " + UNREAD),
    ("receipt commit lacks a prepared file", [EDIT], CHANGES + "none can be named: " + UNREAD
     + "the source commit does not hold every prepared file)"),
    ("moved", [ADD], "recorded repository is unavailable"),  # as at ecddf63
])
def test_an_unreadable_source_still_refuses_and_names_what_it_can(tmp_path, coder, breaks,
                                                                 steps, expected):
    dest, runs, root, result, source = coder
    if breaks == "not a repository":
        shutil.rmtree(source)
        source.mkdir()
    elif breaks == "commit object missing":
        commit = result["source_commit"]
        (source / ".git/objects" / commit[:2] / commit[2:]).unlink()
    elif breaks == "moved":
        shutil.move(source, tmp_path / "moved")
    else:  # the source's earlier commit, before the prepared .gitattributes was added
        earlier = {**result, "source_commit": _git(source, "rev-parse", "HEAD~1")}
        (root / "inputs/coder.json").write_text(json.dumps(earlier))
    _do(dest, steps)
    message = _refusal(runs, dest)
    assert message == expected or (expected.endswith("(") and message.startswith(expected)
                                   and message.endswith(")")), message


def test_a_moved_head_is_named_first_and_a_replacement_needs_a_launch(coder):
    dest, runs, root, result, _ = coder
    _commit(dest, "lane work")
    _do(dest, [("link", "node_modules/react", "../src"), EDIT])
    match = re.fullmatch(r"role inputs changed before first launch: "
                         r"HEAD is ([0-9a-f]{7,40}), not the projection's ([0-9a-f]{7,40})",
                         _refusal(runs, dest))
    assert match and _git(dest, "rev-parse", "HEAD").startswith(match[1])
    assert result["initial_commit"].startswith(match[2])
    assert "no recorded launch" in _refusal(runs, dest, replacing=True)
    (root / "tmux-lanes").mkdir()
    (root / "tmux-lanes/coder-launch.jsonl").write_text(json.dumps(
        {"role": "coder", "repository": str(dest), "status": "active"}) + "\n")
    assert role_inputs.verify(runs, "task", "coder", dest, replacing=True) == result


MIB = 1 << 20
# Pseudo-random, so a read of the wrong block, or the same block twice, differs.
LARGE = {"big/three.bin": hashlib.shake_256(b"three").digest(3 * MIB + 12345),
         "big/two.bin": hashlib.shake_256(b"two").digest(2 * MIB)}


@pytest.mark.parametrize("flips,expected", [
    ([], LINKS + "zz"),
    ([("big/three.bin", 2 * MIB + 777), ("big/two.bin", 2 * MIB - 1)],
     CHANGES + "changed big/three.bin, big/two.bin"),
])
def test_files_longer_than_one_read_block_are_compared_in_full(tmp_path, flips, expected):
    dest, runs, _, _, _ = _clone(tmp_path, LARGE, {}, excludes=())
    if not flips:
        _link_dir(dest / "zz", "big")
    for name, offset in flips:
        data = bytearray(LARGE[name])
        data[offset] ^= 0xFF
        (dest / name).write_bytes(data)
    assert _refusal(runs, dest) == expected
