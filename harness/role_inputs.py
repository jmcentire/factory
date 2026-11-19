"""Prepare independent author inputs from a pinned tree, without its Git history.

The Validator chooses the public interface paths and withheld tests. This code
enforces that selection; it cannot judge whether a document leaks implementation.
The receipt records bytes, not permission or user approval.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath

from factory_runtime.host_git import HostGitError, materialize_commit, run_host_git
from factory_runtime.kindex_workspace import NO_INDEX_MARKER, mark_no_index

record = (importlib.import_module("harness.run_record") if __package__
          else importlib.import_module("run_record"))


def paths(values: list[str]) -> list[PurePosixPath]:
    result = []
    for value in values:
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or path == PurePosixPath(".")
                or ".." in path.parts or "\\" in value or path.as_posix() != value
                or ".git" in path.parts):
            raise ValueError(f"unsafe input path: {value!r}")
        result.append(path)
    return result


def within(path: PurePosixPath, selected: list[PurePosixPath]) -> bool:
    return any(path == item or item in path.parents for item in selected)


def tree_digest(repository: Path) -> str:
    """Inspect actual inputs, including Git-ignored files and assume-unchanged paths."""
    tree = _tree(repository)
    if tree.problems():
        raise ValueError(f"role inputs contain {tree.problems()}")
    return tree.digest


class _Tree:
    """One read of an input tree: its digest, and what the digest cannot take."""

    def __init__(self) -> None:
        self.digest = ""
        self.links: list[str] = []
        self.irregular: list[str] = []
        self.unreadable: list[str] = []
        self.unlisted: list[str] = []

    def problems(self) -> str:
        """Every directory symlink, non-regular file, unreadable file or directory ("" if none)."""
        kinds = (("a directory symlink", self.links), ("a non-regular file", self.irregular),
                 ("a file that cannot be read", self.unreadable),
                 ("a directory that cannot be read", self.unlisted))
        return "; ".join(f"{kind}: {_named(sorted(found))}" for kind, found in kinds if found)


def _tree(repository: Path) -> _Tree:
    """The tree digest, read to the end: a refusal names every problem, not the first.

    Directory symlinks and non-regular files are neither followed nor digested; a file or
    directory (``dir/``) that cannot be read is named and the walk goes on. The digest of a tree
    with none of them is the digest this module has always computed.
    """
    tree, digest = _Tree(), hashlib.sha256()

    def unlisted(error: OSError) -> None:
        if error.filename is not None:
            name = Path(error.filename).relative_to(repository).as_posix()
            tree.unlisted.append(name + "/")

    for base, directories, files in os.walk(repository, onerror=unlisted, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if not (Path(base) == repository and name == ".git"))
        tree.links += [(Path(base) / directory).relative_to(repository).as_posix()
                       for directory in directories if (Path(base) / directory).is_symlink()]
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(repository).as_posix()
            try:
                mode = path.lstat().st_mode
                if not (stat.S_ISLNK(mode) or stat.S_ISREG(mode)):
                    tree.irregular.append(relative)
                    continue
                digest.update(relative.encode() + b"\0")
                digest.update(str(stat.S_IMODE(mode)).encode() + b"\0")
                if stat.S_ISLNK(mode):
                    digest.update(b"link\0" + os.readlink(path).encode())
                elif tree.links or tree.irregular or tree.unreadable or tree.unlisted:
                    with path.open("rb"):
                        pass  # refused anyway, so the digest is moot: an install isn't read
                else:
                    digest.update(b"file\0")
                    with path.open("rb") as stream:
                        while block := stream.read(1024 * 1024):
                            digest.update(block)
                digest.update(b"\0")
            except OSError:
                tree.unreadable.append(relative)  # refused below; the digest no longer matters
    tree.digest = digest.hexdigest()
    return tree


#: How many paths of each kind a refusal names before it counts the rest.
NAMED_PATHS = 5


def _named(found: list[str]) -> str:
    more = f" and {len(found) - NAMED_PATHS} more" if len(found) > NAMED_PATHS else ""
    return ", ".join(found[:NAMED_PATHS]) + more


#: The mode ``prepare`` gives a regular file (materialize_commit's, kept by its copy).
_PREPARED_MODES = {"100644": 0o644, "100755": 0o755}
#: The no-index marker's mode as ``mark_no_index`` creates it.
_MARKER_MODE = 0o600


def _source_listing(receipt: dict, source: Path) -> tuple[dict[str, tuple[str, str]], str]:
    """The prepared files' modes and blob ids at the source commit, and its hash algorithm.

    ``prepare`` writes those blobs' raw bytes (``materialize_commit``: no attribute or filter
    applies), so they are exactly what an untouched clone holds. The source repository is the
    operator's, not the author clone, and ``ls-tree`` runs no filter or other program. Only the
    receipt's own files are kept: nothing the projection withheld is named.
    ValueError when the source commit cannot be read."""
    try:
        algorithm = run_host_git(source, ["rev-parse", "--show-object-format"]).decode().strip()
        listing = run_host_git(source, ["ls-tree", "-r", "-z", "--full-tree",
                                        str(receipt["source_commit"])])
    except (HostGitError, OSError, KeyError) as error:
        raise ValueError(str(error)) from error
    files = set(receipt["files"])
    entries: dict[str, tuple[str, str]] = {}
    for row in listing.split(b"\0"):
        meta, _, listed = row.partition(b"\t")
        name = os.fsdecode(listed)
        if name in files:
            mode, _, oid = meta.decode().split(" ")
            entries[name] = (mode, oid)
    if len(entries) != len(files):
        raise ValueError("the source commit does not hold every prepared file")
    return entries, algorithm


def unrecorded_changes(receipt: dict, repository: Path, tree: _Tree,
                       source: Path) -> tuple[str, str]:
    """Name what was added, removed or changed since preparation, and what could not be.

    Nothing extra is kept at preparation: the receipt lists the prepared files, and the source
    commit (``_source_listing``) holds their modes and bytes, so they are compared only when a
    launch is refused. Every file is hashed here, as raw bytes: nothing in the author clone
    (its attributes, filters or config) is consulted or run. A file's mode is compared with the
    one ``prepare`` gave it. A directory the projection never had (an install's node_modules) is
    named once, as ``dir/``, and not read; one holding only empty folders is not named, as no
    input check sees it. A path that is now a directory symlink, another non-regular file or
    unreadable is named in ``tree.problems()`` only, and so is what was prepared under it; a
    file the walk could open but this comparison cannot read is added to those, beside
    everything else found. When the source commit cannot be read, what was added or removed is
    still named, and the second value says why the rest is not.
    """
    expected = set(receipt["files"])
    try:
        prepared, algorithm = _source_listing(receipt, source)
        unnamed = ""
    except ValueError as error:
        prepared, algorithm = {}, ""
        unnamed = f"the source commit could not be read ({error})"
    folders = {parent.as_posix() for name in expected for parent in PurePosixPath(name).parents}
    shown = set(tree.links) | set(tree.irregular) | set(tree.unreadable)
    hidden = shown | {name.rstrip("/") for name in tree.unlisted}
    added: list[str] = []
    changed: list[str] = []
    present: set[str] = set()
    for base, directories, files in os.walk(repository, followlinks=False):  # errors: in tree
        here = Path(base)
        kept = []
        for directory in sorted(directories):
            relative = (here / directory).relative_to(repository).as_posix()
            if (here == repository and directory == ".git") or (here / directory).is_symlink():
                continue
            if relative in folders:
                kept.append(directory)
            elif _holds_entries(here / directory, relative, tree.unlisted):
                added.append(relative + "/")  # never prepared: named once, not read
        directories[:] = kept
        for name in sorted(files):
            path = here / name
            relative = path.relative_to(repository).as_posix()
            if relative in shown:
                continue
            present.add(relative)
            try:
                info = path.lstat()
                if relative == NO_INDEX_MARKER:
                    if (not stat.S_ISREG(info.st_mode) or info.st_size
                            or stat.S_IMODE(info.st_mode) != _MARKER_MODE):
                        changed.append(relative)
                elif relative not in expected:
                    added.append(relative)
                elif relative not in prepared:
                    continue  # the source commit could not be read: said in ``unnamed``
                elif stat.S_ISLNK(info.st_mode) != (prepared[relative][0] == "120000"):
                    changed.append(relative)  # a file became a link, or a link a file
                elif (not stat.S_ISLNK(info.st_mode) and stat.S_IMODE(info.st_mode)
                      != _PREPARED_MODES.get(prepared[relative][0])):
                    changed.append(relative)
                elif _blob_id(path, algorithm) != prepared[relative][1]:
                    changed.append(relative)
            except OSError:
                tree.unreadable.append(relative)  # opened by the walk, but not readable here
    removed = [name for name in [*expected, NO_INDEX_MARKER]
               if name not in present and name not in hidden
               and not any(parent.as_posix() in hidden for parent in PurePosixPath(name).parents)]
    kinds = (("added", sorted(added)), ("removed", sorted(removed)), ("changed", sorted(changed)))
    return "; ".join(f"{kind} {_named(found)}" for kind, found in kinds if found), unnamed


def _holds_entries(directory: Path, relative: str, unlisted: list[str]) -> bool:
    """Whether a new directory holds anything but plain directories (a file, a link, or a
    directory that cannot be listed, which is named as unreadable as well).

    It stops at the first such entry, so an install's node_modules costs one listing. One that
    itself cannot be listed is named as unreadable only, not as added."""
    if any(name.startswith(relative + "/") and name != relative + "/" for name in unlisted):
        return True
    for base, directories, files in os.walk(directory, followlinks=False):
        if files or any((Path(base) / name).is_symlink() for name in directories):
            return True
    return False


def _blob_id(path: Path, algorithm: str) -> str:
    """A link's or file's Git blob id of its raw bytes, read in bounded blocks."""
    hasher = hashlib.new("sha256" if algorithm == "sha256" else "sha1")
    if path.is_symlink():
        target = os.fsencode(os.readlink(path))
        hasher.update(b"blob %d\0" % len(target) + target)
        return hasher.hexdigest()
    hasher.update(b"blob %d\0" % path.lstat().st_size)
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def prepare(runs: Path, run: str, role: str, destination: Path,
            includes: list[str], excludes: list[str]) -> dict:
    if role not in {"coder", "tester"}:
        raise ValueError("role must be coder or tester")
    if role == "coder" and includes:
        raise ValueError("Coder receives the source minus --exclude paths")
    if role == "tester" and excludes:
        raise ValueError("Tester receives only --include interface/specification paths")
    selected, withheld = paths(includes), paths(excludes)
    # A live or historical shared knowledge graph can disclose either author's work.
    # lane_kindex supplies the Validator's curated shared seed later.
    withheld.append(PurePosixPath(".kin"))
    withheld.append(PurePosixPath(NO_INDEX_MARKER))
    root, task = record.load(runs, run)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("input destination exists; never overwrite an author's work")
    destination = destination.parent.resolve(strict=True) / destination.name
    if (destination.is_relative_to(root) or root.is_relative_to(destination)
            or destination.is_relative_to(Path(task["repository"]))):
        raise ValueError("author inputs must be separate from control and source repositories")
    receipts = root / "inputs"
    if receipts.is_symlink():
        raise ValueError("input receipts directory is a symlink")
    receipts.mkdir(mode=0o700, exist_ok=True)
    receipt_path = receipts / f"{role}.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("role inputs already prepared; preserve the resident author's context")
    with tempfile.TemporaryDirectory(prefix="factory-inputs-") as temporary:
        mark_no_index(Path(temporary))
        source = Path(temporary).resolve() / "tree"
        listed: list[PurePosixPath] = []
        omitted: list[str] = []

        def select(path: PurePosixPath) -> bool:
            listed.append(path)
            return (NO_INDEX_MARKER not in path.parts and not within(path, withheld)
                    and (role == "coder" or within(path, selected)))

        def directory_link(path: PurePosixPath) -> None:
            if within(path, selected):
                raise ValueError(f"selected input is a link to a directory: {path}")
            omitted.append(path.as_posix())

        # Withheld paths and in-tree directory links the role did not include are never
        # materialized; the real directory is still delivered when selected.
        materialize_commit(Path(task["repository"]), task["resolved_commit"], source,
                           admit_symlinks=True, contained_file_links=True, select=select,
                           directory_link=directory_link,
                           max_files=1000000, max_total_bytes=16 * 1024**3)
        for selection in selected + paths(excludes):
            if not any(within(path, [selection]) for path in listed):
                raise ValueError(f"selected input path is absent at the base commit: {selection}")
        files = [p for p in source.rglob("*") if p.is_file() or p.is_symlink()]
        kept = {p.relative_to(source).as_posix() for p in files
                if not within(PurePosixPath(p.relative_to(source)), withheld)
                and (role == "coder" or within(PurePosixPath(p.relative_to(source)), selected))}
        for name in kept:
            item = source / name
            # exists() follows the link as the author's OS will; resolve() alone is lexical
            # through missing directories.
            if item.is_symlink() and (not item.exists()
                                      or item.resolve().relative_to(source).as_posix() not in kept):
                raise ValueError(f"selected symlink refers to withheld input: {name}")
        destination.mkdir(mode=0o700)
        mark_no_index(destination)
        for name in sorted(kept):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target, follow_symlinks=False)
    # A fresh object database cannot reveal omitted paths through git show or git log.
    run_host_git(destination, ["init", "-q", "-b", f"lane/{role}"])
    # The marker is local runtime state, never part of an author's work product.
    with (destination / ".git/info/exclude").open("a", encoding="utf-8") as stream:
        stream.write(f"\n/{NO_INDEX_MARKER}\n")
    run_host_git(destination, ["add", "-f", "--", ".", f":(top,exclude){NO_INDEX_MARKER}"])
    run_host_git(destination, ["-c", "user.name=Factory", "-c", "user.email=factory@local",
                               "commit", "--allow-empty", "-qm", f"{role} input projection"])
    initial = run_host_git(destination, ["rev-parse", "HEAD"]).decode().strip()
    result = dict(role=role, repository=str(destination), source_commit=task["resolved_commit"],
                  initial_commit=initial, includes=includes, excludes=excludes,
                  files=sorted(kept), omitted_directory_links=omitted)
    result["tree_digest"] = tree_digest(destination)
    record.write_new(receipt_path, (json.dumps(result, indent=2) + "\n").encode())
    return result


def _launched(root: Path, role: str, repository: Path) -> bool:
    """Whether the run's launch journals record an active launch of role in repository."""
    lanes = root / "tmux-lanes"
    if lanes.is_symlink() or not lanes.is_dir():
        return False
    for journal in sorted(lanes.glob(f"{role}*-launch.jsonl")):
        if journal.is_symlink():
            continue
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (isinstance(row, dict) and row.get("status") == "active"
                    and row.get("role") == role
                    and Path(str(row.get("repository", ""))).resolve() == repository.resolve()):
                return True
    return False


def verify(runs: Path, run: str, role: str, repository: Path, *, replacing: bool = False) -> dict:
    root, task = record.load(runs, run)
    if (root / "inputs").is_symlink():
        raise ValueError("input receipts directory is a symlink")
    receipt = record.read_json(root / "inputs" / f"{role}.json")
    if receipt["role"] != role or Path(receipt["repository"]) != repository.resolve():
        raise ValueError("repository is not this role's prepared input projection")
    initial = run_host_git(repository, ["rev-parse", "HEAD"]).decode().strip()
    if replacing:
        # A replacement round continues a clone this role was launched in: its commits are the
        # lane's work. Nothing launched there yet means a first launch, checked in full below.
        if not _launched(root, role, repository):
            raise ValueError("no recorded launch of this role in this repository to replace")
        try:
            run_host_git(repository, ["merge-base", "--is-ancestor", receipt["initial_commit"],
                                      initial])
        except HostGitError as error:
            raise ValueError("role inputs are not descended from the prepared projection") \
                from error
        return receipt
    if initial != receipt["initial_commit"]:
        raise ValueError(f"role inputs changed before first launch: HEAD is {initial[:12]}, "
                         f"not the projection's {receipt['initial_commit'][:12]}")
    marker = repository / NO_INDEX_MARKER
    if not marker.exists() and not marker.is_symlink():
        # `git clean -x` (e.g. undoing a pre-launch install) removes this runtime marker;
        # it holds no input, so put it back as prepared rather than refuse.
        mark_no_index(repository)
    tree = _tree(repository)
    if tree.problems() or tree.digest != receipt["tree_digest"]:
        # Everything to undo, in one refusal: an author clone used before launch usually has
        # several (an install leaves node_modules links and a hook directory).
        changes, unnamed = unrecorded_changes(receipt, repository, tree,
                                              Path(task["repository"]))
        if tree.problems():
            raise ValueError(f"role inputs contain {tree.problems()}"
                             + (f"; and unrecorded changes: {changes}" if changes else "")
                             + (f"; other changes cannot be named: {unnamed}" if unnamed else ""))
        if changes:
            raise ValueError("role inputs have unrecorded changes before first launch: " + changes
                             + (f"; other changes cannot be named: {unnamed}" if unnamed else ""))
        raise ValueError("role inputs have unrecorded changes before first launch: "
                         + (f"none can be named: {unnamed}" if unnamed else
                            "none that the projection's source commit can show"))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify"])
    parser.add_argument("--runs", type=Path,
                        default=Path(os.environ.get("FACTORY_RUNS_DIR", ".factory/runs")))
    parser.add_argument("--run", required=True)
    parser.add_argument("--role", choices=["coder", "tester"], required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--replacing", action="store_true",
                        help="a replacement round's clone: HEAD descends from the projection")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.runs, args.run, args.role, args.repo, args.include, args.exclude)
        else:
            result = verify(args.runs, args.run, args.role, args.repo, replacing=args.replacing)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"role inputs: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
