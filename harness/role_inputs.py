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


def unrecorded_changes(receipt: dict, repository: Path, tree: _Tree) -> str:
    """Name what was added, removed or changed since the projection's own first commit.

    Nothing extra is kept at preparation: the clone's first commit holds every input file but
    the no-index marker, so it is compared only when a launch is refused. Content and the
    executable bit are compared, as Git records them. A directory the commit never had (an
    install's node_modules) is named once, as ``dir/``, and not read. A path that is now a
    directory symlink, another non-regular file or unreadable is named in ``tree.problems()``
    only, and so is what the commit held under it.
    """
    initial = str(receipt["initial_commit"])
    try:
        algorithm = run_host_git(repository, ["rev-parse", "--show-object-format"]).decode().strip()
        listing = run_host_git(repository, ["ls-tree", "-r", "-z", "--full-tree", initial])
    except (HostGitError, OSError, ValueError):
        return ""
    committed: dict[str, tuple[str, str]] = {}
    for row in listing.split(b"\0"):
        if not row:
            continue
        meta, _, listed = row.partition(b"\t")
        mode, kind, oid = meta.decode().split(" ")
        if kind == "blob":
            committed[listed.decode()] = (mode, oid)
    folders = {parent.as_posix() for name in committed for parent in PurePosixPath(name).parents}
    shown = set(tree.links) | set(tree.irregular) | set(tree.unreadable)
    hidden = set(tree.links) | {name.rstrip("/") for name in tree.unlisted}
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
            else:
                added.append(relative + "/")  # never committed: named once, not read
        directories[:] = kept
        for name in sorted(files):
            path = here / name
            relative = path.relative_to(repository).as_posix()
            if relative in shown:
                continue
            present.add(relative)
            if relative == NO_INDEX_MARKER:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size:
                    changed.append(relative)
                continue
            if relative not in committed:
                added.append(relative)
            elif _differs(path, *committed[relative], algorithm):
                changed.append(relative)
    removed = [name for name in [*committed, NO_INDEX_MARKER]
               if name not in present and name not in shown
               and not any(parent.as_posix() in hidden for parent in PurePosixPath(name).parents)]
    kinds = (("added", sorted(added)), ("removed", sorted(removed)), ("changed", sorted(changed)))
    return "; ".join(f"{kind} {_named(found)}" for kind, found in kinds if found)


def _differs(path: Path, mode: str, oid: str, algorithm: str) -> bool:
    """Whether a file differs from its committed blob: type, content or executable bit."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        if mode != "120000":
            return True
        data = os.readlink(path).encode()
    else:
        # Git records one executable bit, the owner's; other mode bits are not compared.
        if mode == "120000" or (mode == "100755") != bool(info.st_mode & stat.S_IXUSR):
            return True
        data = path.read_bytes()
    hasher = hashlib.new("sha256" if algorithm == "sha256" else "sha1")
    hasher.update(b"blob %d\0" % len(data) + data)
    return hasher.hexdigest() != oid


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
    root, _ = record.load(runs, run)
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
    tree = _tree(repository)
    if tree.problems() or tree.digest != receipt["tree_digest"]:
        # Everything to undo, in one refusal: an author clone used before launch usually has
        # several (an install leaves node_modules links and a hook directory).
        changes = unrecorded_changes(receipt, repository, tree)
        if tree.problems():
            raise ValueError(f"role inputs contain {tree.problems()}"
                             + (f"; and unrecorded changes: {changes}" if changes else ""))
        raise ValueError("role inputs have unrecorded changes before first launch: "
                         + (changes or "permission bits that Git does not record"))
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
