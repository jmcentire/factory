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

from factory_runtime.host_git import materialize_commit, run_host_git

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
    digest = hashlib.sha256()
    for base, directories, files in os.walk(repository, followlinks=False):
        directories[:] = sorted(name for name in directories
                                if not (Path(base) == repository and name == ".git"))
        for directory in directories:
            if (Path(base) / directory).is_symlink():
                raise ValueError("role inputs contain a directory symlink")
        for name in sorted(files):
            path = Path(base) / name
            mode = path.lstat().st_mode
            digest.update(path.relative_to(repository).as_posix().encode() + b"\0")
            digest.update(str(stat.S_IMODE(mode)).encode() + b"\0")
            if stat.S_ISLNK(mode):
                digest.update(b"link\0" + os.readlink(path).encode())
            elif stat.S_ISREG(mode):
                digest.update(b"file\0")
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        digest.update(block)
            else:
                raise ValueError("role inputs contain a non-regular file")
            digest.update(b"\0")
    return digest.hexdigest()


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
        source = Path(temporary).resolve() / "tree"
        materialize_commit(Path(task["repository"]), task["resolved_commit"], source,
                           admit_symlinks=True, contained_file_links=True,
                           max_files=1000000, max_total_bytes=16 * 1024**3)
        for selection in selected + paths(excludes):
            if not (source / selection).exists():
                raise ValueError(f"selected input path is absent at the base commit: {selection}")
        files = [p for p in source.rglob("*") if p.is_file() or p.is_symlink()]
        kept = {p.relative_to(source).as_posix() for p in files
                if not within(PurePosixPath(p.relative_to(source)), withheld)
                and (role == "coder" or within(PurePosixPath(p.relative_to(source)), selected))}
        for name in kept:
            item = source / name
            if item.is_symlink() and item.resolve().relative_to(source).as_posix() not in kept:
                raise ValueError(f"selected symlink refers to withheld input: {name}")
        destination.mkdir(mode=0o700)
        for name in sorted(kept):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target, follow_symlinks=False)
    # A fresh object database cannot reveal omitted paths through git show or git log.
    run_host_git(destination, ["init", "-q", "-b", f"lane/{role}"])
    run_host_git(destination, ["add", "-f", "."])
    run_host_git(destination, ["-c", "user.name=Factory", "-c", "user.email=factory@local",
                               "commit", "--allow-empty", "-qm", f"{role} input projection"])
    initial = run_host_git(destination, ["rev-parse", "HEAD"]).decode().strip()
    result = dict(role=role, repository=str(destination), source_commit=task["resolved_commit"],
                  initial_commit=initial, includes=includes, excludes=excludes,
                  files=sorted(kept), tree_digest=tree_digest(destination))
    record.write_new(receipt_path, (json.dumps(result, indent=2) + "\n").encode())
    return result


def verify(runs: Path, run: str, role: str, repository: Path) -> dict:
    root, _ = record.load(runs, run)
    if (root / "inputs").is_symlink():
        raise ValueError("input receipts directory is a symlink")
    receipt = record.read_json(root / "inputs" / f"{role}.json")
    if receipt["role"] != role or Path(receipt["repository"]) != repository.resolve():
        raise ValueError("repository is not this role's prepared input projection")
    initial = run_host_git(repository, ["rev-parse", "HEAD"]).decode().strip()
    if initial != receipt["initial_commit"]:
        raise ValueError("role inputs changed before first launch")
    if tree_digest(repository) != receipt["tree_digest"]:
        raise ValueError("role inputs have unrecorded changes before first launch")
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
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.runs, args.run, args.role, args.repo, args.include, args.exclude)
        else:
            result = verify(args.runs, args.run, args.role, args.repo)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"role inputs: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
