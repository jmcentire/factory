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
    digest, unreadable = _tree(repository, entries=None)
    if unreadable:
        raise ValueError(f"role inputs contain {unreadable}")
    return digest


def tree_entries(repository: Path) -> dict[str, str]:
    """Each input file's own short digest, so a refusal can name what changed.

    Diagnosis only: the tree digest above is what a launch is checked against. Like it, this
    covers every file but ``.git``, the no-index marker included.
    """
    entries: dict[str, str] = {}
    _tree(repository, entries=entries)
    return entries


def _digest_and_entries(repository: Path) -> tuple[str, dict[str, str]]:
    """Both, from one read of the tree."""
    entries: dict[str, str] = {}
    digest, unreadable = _tree(repository, entries=entries)
    if unreadable:
        raise ValueError(f"role inputs contain {unreadable}")
    return digest, entries


def _tree(repository: Path, *, entries: dict[str, str] | None) -> tuple[str, str]:
    """The tree digest, and what it cannot take: directory symlinks and non-regular files.

    Neither is followed or digested. The whole tree is read even when one is found, so a
    refusal names every one of them ("" when there are none).
    """
    digest = hashlib.sha256()
    links: list[str] = []
    irregular: list[str] = []
    for base, directories, files in os.walk(repository, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if not (Path(base) == repository and name == ".git"))
        links += [(Path(base) / directory).relative_to(repository).as_posix()
                  for directory in directories if (Path(base) / directory).is_symlink()]
        for name in sorted(files):
            path = Path(base) / name
            relative = path.relative_to(repository).as_posix()
            mode = path.lstat().st_mode
            if not (stat.S_ISLNK(mode) or stat.S_ISREG(mode)):
                irregular.append(relative)
                continue
            # The tree digest's bytes are unchanged; an entry digest reads the same bytes.
            hashers = [digest] if entries is None else [digest, hashlib.sha256()]
            parts: list[bytes] = [relative.encode() + b"\0",
                                  str(stat.S_IMODE(mode)).encode() + b"\0"]
            parts.append(b"link\0" + os.readlink(path).encode() if stat.S_ISLNK(mode)
                         else b"file\0")
            for part in parts:
                for hasher in hashers:
                    hasher.update(part)
            if stat.S_ISREG(mode):
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        for hasher in hashers:
                            hasher.update(block)
            for hasher in hashers:
                hasher.update(b"\0")
            if entries is not None:
                entries[relative] = hashers[1].hexdigest()[:16]
    unreadable = []
    if links:
        unreadable.append(f"a directory symlink: {_named(sorted(links))}")
    if irregular:
        unreadable.append(f"a non-regular file: {_named(sorted(irregular))}")
    return digest.hexdigest(), "; ".join(unreadable)


#: How many paths of each kind a refusal names before it counts the rest.
NAMED_PATHS = 5


def _named(found: list[str]) -> str:
    more = f" and {len(found) - NAMED_PATHS} more" if len(found) > NAMED_PATHS else ""
    return ", ".join(found[:NAMED_PATHS]) + more


def unrecorded_changes(receipt: dict, repository: Path) -> str:
    """Name the input files added, removed or changed since the projection was prepared."""
    recorded = receipt.get("file_digests")
    current = tree_entries(repository)
    if isinstance(recorded, dict):
        before = {str(path): str(value) for path, value in recorded.items()}
    else:  # a receipt from before file digests: added and removed files only
        listed = [*receipt.get("files", []), NO_INDEX_MARKER]  # the marker is never listed
        before = {str(path): current.get(str(path), "") for path in listed}
        if NO_INDEX_MARKER not in current:
            before.pop(NO_INDEX_MARKER)  # unknown whether it was there: say nothing about it
    kinds = (("added", sorted(set(current) - set(before))),
             ("removed", sorted(set(before) - set(current))),
             ("changed", sorted(path for path in set(before) & set(current)
                                if before[path] != current[path])))
    return "; ".join(f"{kind} {_named(found)}" for kind, found in kinds if found)


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
    result["tree_digest"], result["file_digests"] = _digest_and_entries(destination)
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
    digest, unreadable = _tree(repository, entries=None)
    if unreadable or digest != receipt["tree_digest"]:
        # Everything to undo, in one refusal: an author clone used before launch usually has
        # several (an install leaves node_modules links and a hook directory).
        changes = unrecorded_changes(receipt, repository)
        if unreadable:
            raise ValueError(f"role inputs contain {unreadable}"
                             + (f"; and unrecorded changes: {changes}" if changes else ""))
        raise ValueError("role inputs have unrecorded changes before first launch: "
                         + (changes or "file contents (this receipt has no per-file digests)"))
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
