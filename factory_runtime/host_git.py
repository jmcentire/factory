"""Host Git over a repository an agent could have written.

From the moment an agent starts, its repository, ``.git`` included, is agent-owned
(docs/HARNESS.md). Ordinary host Git run there does two things the factory forbids:

* It executes what the agent planted. ``git status`` runs ``core.fsmonitor``; ``git commit``
  runs ``.git/hooks``; ``git archive`` runs any ``filter.<driver>.smudge`` the repository's
  own config defines; an ``ext::`` remote is a shell command.
* It reads bytes other than the named commit's. ``refs/replace`` substitutes another commit
  under the same object id; ``export-ignore`` in ``.git/info/attributes`` drops paths (a failing
  test, say) from what ``git archive`` emits; ``export-subst`` and smudge filters rewrite
  content. A verdict that names commit X while judging those bytes judged something else.

This module is the one way harness code runs Git against such a repository:

* ``run_host_git`` runs Git in a closed environment: no system or global config, no replace
  objects, no system attributes, no prompt, and overrides carried in ``GIT_CONFIG_COUNT``.
  Those take command-line precedence, so nothing in the repository's own config outranks them:
  hooks go to the null device, fsmonitor is off, every transport is refused, and the attributes
  file and pager are neutral.
* ``materialize_commit`` writes a commit's tree from raw objects (``ls-tree`` and ``cat-file
  --batch``). No attribute, filter or replacement can change a byte: what lands on disk is the
  commit's blobs at the commit's modes, or the call refuses.

Git has no configuration switch that disables a repository-defined ``diff.external`` or
``filter`` driver. Nothing here converts content, and a caller that diffs passes
``--no-ext-diff``.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_TRANSPORTS = ("ext", "fd", "file", "git", "ssh", "http", "https")

# Each override is a (key, value) pair Git reads as if given with ``-c``. Values are chosen so
# the key is inert, never so that it names something to run.
_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", "false"),
    ("core.attributesFile", os.devnull),
    ("core.pager", "cat"),
    # ``protocol.<name>.allow`` outranks ``protocol.allow`` whatever the config source, so a
    # repository that allows ``ext`` itself would win over a bare ``protocol.allow=never``.
    ("protocol.allow", "never"),
    *((f"protocol.{name}.allow", "never") for name in _TRANSPORTS),
    ("credential.helper", ""),
    ("log.showSignature", "false"),
)

_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REGULAR_MODES = {"100644": 0o644, "100755": 0o755}
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class HostGitError(RuntimeError):
    """Host Git could not read the repository without trusting what the agent wrote."""


@dataclass(frozen=True)
class MaterializedCommit:
    commit: str
    tree: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class _Entry:
    mode: str
    object_id: str
    path: PurePosixPath


def _closed_environment(path: str) -> dict[str, str]:
    """The whole environment a host Git process gets; nothing is inherited but ``PATH``."""

    environment = {
        "PATH": path,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": str(len(_OVERRIDES)),
    }
    for index, (key, value) in enumerate(_OVERRIDES):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment


def run_host_git(
    repository: str | Path,
    arguments: Sequence[str],
    *,
    runner: Runner = subprocess.run,
    stdin: bytes | None = None,
    timeout: float = 300,
) -> bytes:
    """Run one Git command in ``repository`` under the closed environment; stdout or raise."""

    try:
        result = runner(
            ["git", "-C", os.fspath(repository), *arguments],
            input=stdin if stdin is not None else b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=_closed_environment(os.environ.get("PATH", os.defpath)),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostGitError(f"git {_verb(arguments)} could not run: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise HostGitError(
            f"git {_verb(arguments)} failed in {repository}: "
            f"{detail or f'exited {result.returncode} with no output'}"
        )
    return result.stdout


def materialize_commit(
    repository: str | Path,
    commit: str,
    destination: str | Path,
    *,
    admit_symlinks: bool,
    max_files: int,
    max_total_bytes: int,
    runner: Runner = subprocess.run,
) -> MaterializedCommit:
    """Write exactly ``commit``'s tree into a new ``destination`` from raw Git objects."""

    tree = resolve_commit_tree(repository, commit, runner=runner)
    entries = _tree_entries(
        run_host_git(repository, ("ls-tree", "-rz", "--full-tree", commit), runner=runner),
        admit_symlinks=admit_symlinks,
        max_files=max_files,
    )
    blobs = _read_blobs(repository, entries, max_total_bytes=max_total_bytes, runner=runner)
    _write_tree(Path(destination), entries, blobs)
    return MaterializedCommit(
        commit=commit,
        tree=tree,
        file_count=len(entries),
        total_bytes=sum(len(blob) for blob in blobs.values()),
    )


def resolve_commit_tree(
    repository: str | Path, commit: str, *, runner: Runner = subprocess.run
) -> str:
    """The tree of exactly ``commit``: a full object id that names itself, never a replacement."""

    if not _OBJECT_ID.fullmatch(commit):
        raise HostGitError("commit must be a full lowercase object id, never a ref or prefix")
    resolved = run_host_git(
        repository,
        ("rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"),
        runner=runner,
    ).decode("ascii", errors="replace").strip()
    if resolved != commit:
        raise HostGitError(f"{commit} does not name itself as a commit in {repository}")
    return (
        run_host_git(
            repository,
            ("rev-parse", "--verify", "--end-of-options", f"{commit}^{{tree}}"),
            runner=runner,
        )
        .decode("ascii", errors="replace")
        .strip()
    )


def _verb(arguments: Sequence[str]) -> str:
    return arguments[0] if arguments else "(no command)"


def _tree_entries(listing: bytes, *, admit_symlinks: bool, max_files: int) -> list[_Entry]:
    """Parse ``ls-tree -rz`` output into entries every later step can write safely."""

    entries: list[_Entry] = []
    seen: dict[str, str] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        entry = _parse_entry(record)
        if entry.mode == _GITLINK_MODE:
            raise HostGitError(
                f"{entry.path} is a submodule: its content is not in this commit, so the "
                "commit alone cannot be judged"
            )
        if entry.mode == _SYMLINK_MODE and not admit_symlinks:
            raise HostGitError(f"{entry.path} is a symbolic link, which this reader refuses")
        if entry.mode not in _REGULAR_MODES and entry.mode != _SYMLINK_MODE:
            raise HostGitError(f"{entry.path} has unsupported Git mode {entry.mode}")
        for folded, original in _portable_prefixes(entry.path):
            if seen.setdefault(folded, original) != original:
                raise HostGitError(
                    f"{original} and {seen[folded]} collide on a case-insensitive filesystem"
                )
        entries.append(entry)
        if len(entries) > max_files:
            raise HostGitError(f"commit has more than {max_files} files")
    return entries


def _parse_entry(record: bytes) -> _Entry:
    try:
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        text = raw_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HostGitError("Git tree listing is malformed") from exc
    if kind not in {"blob", "commit"} or not _OBJECT_ID.fullmatch(object_id):
        raise HostGitError(f"Git tree listing has an unexpected entry for {text!r}")
    path = PurePosixPath(text)
    parts = path.parts
    if (
        not parts
        or path.is_absolute()
        or any(part in {"", ".", ".."} or "\0" in part for part in parts)
        or any(part.casefold() == ".git" for part in parts)
    ):
        raise HostGitError(f"Git tree path {text!r} would leave or shadow the checkout")
    return _Entry(mode=mode, object_id=object_id, path=path)


def _portable_prefixes(path: PurePosixPath) -> list[tuple[str, str]]:
    """Each directory prefix and the path itself, paired with its case-folded spelling, so
    ``A/x`` beside ``A/y`` shares a directory while ``A/x`` beside ``a/y`` collides."""

    parts = path.parts
    return [
        ("/".join(parts[: index + 1]).casefold(), "/".join(parts[: index + 1]))
        for index in range(len(parts))
    ]


def _read_blobs(
    repository: str | Path,
    entries: Sequence[_Entry],
    *,
    max_total_bytes: int,
    runner: Runner,
) -> Mapping[str, bytes]:
    """Read every blob once, raw, through ``cat-file`` (no filters, no textconv).

    Sizes are checked with ``--batch-check`` before any content is read, so the byte ceiling
    bounds memory as well as the result."""

    wanted = sorted({entry.object_id for entry in entries})
    if not wanted:
        return {}
    request = "".join(f"{object_id}\n" for object_id in wanted).encode("ascii")
    sizes = _blob_sizes(
        run_host_git(repository, ("cat-file", "--batch-check"), stdin=request, runner=runner),
        wanted,
    )
    if sum(sizes.values()) > max_total_bytes:
        raise HostGitError(f"commit content exceeds {max_total_bytes} bytes")
    output = run_host_git(repository, ("cat-file", "--batch"), stdin=request, runner=runner)
    blobs: dict[str, bytes] = {}
    offset = 0
    for object_id in wanted:
        header = f"{object_id} blob {sizes[object_id]}\n".encode("ascii")
        start = offset + len(header)
        end = start + sizes[object_id]
        if output[offset:start] != header or output[end : end + 1] != b"\n":
            raise HostGitError(f"object {object_id} changed or was truncated while being read")
        blobs[object_id] = output[start:end]
        offset = end + 1
    return blobs


def _blob_sizes(check: bytes, wanted: Sequence[str]) -> dict[str, int]:
    lines = check.decode("ascii", errors="replace").splitlines()
    if len(lines) != len(wanted):
        raise HostGitError("cat-file did not answer for every object")
    sizes: dict[str, int] = {}
    for object_id, line in zip(wanted, lines, strict=True):
        fields = line.split(" ")
        if len(fields) != 3 or fields[0] != object_id or fields[1] != "blob":
            raise HostGitError(f"object {object_id} is missing or is not a blob: {line}")
        if not fields[2].isdigit():
            raise HostGitError(f"object {object_id} has no readable size: {line}")
        sizes[object_id] = int(fields[2])
    return sizes


def _write_tree(
    destination: Path, entries: Sequence[_Entry], blobs: Mapping[str, bytes]
) -> None:
    """Create ``destination`` and write regular files, then links, never following a link."""

    try:
        destination.mkdir(mode=0o755)
    except OSError as exc:
        raise HostGitError(f"destination must be a new directory: {exc}") from exc
    ordered = sorted(entries, key=lambda entry: entry.mode == _SYMLINK_MODE)
    for entry in ordered:
        target = destination.joinpath(*entry.path.parts)
        try:
            _make_parents(destination, entry.path)
            data = blobs[entry.object_id]
            if entry.mode == _SYMLINK_MODE:
                os.symlink(os.fsdecode(data), target)
                continue
            _write_file(target, data, _REGULAR_MODES[entry.mode])
        except (OSError, ValueError) as exc:
            raise HostGitError(f"could not write {entry.path}: {exc}") from exc


def _make_parents(destination: Path, path: PurePosixPath) -> None:
    current = destination
    for part in path.parts[:-1]:
        current = current / part
        try:
            current.mkdir(mode=0o755)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise HostGitError(f"{current} is not a directory") from None


def _write_file(target: Path, data: bytes, mode: int) -> None:
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.chmod(target, mode)
