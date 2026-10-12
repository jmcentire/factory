#!/usr/bin/env python3
"""Produce an exact-subject cross-path mismatch witness.

The witness is deliberately asymmetric.  A producer-side or consumer-side mutation must leave
the selected local suite green while making the selected agreement oracle red.  This demonstrates
that the oracle checks composition which the individual path suite does not already check.  It
does not permit deleting or weakening either suite: their byte manifests are re-derived after the
mutation and bound into the receipt together with the exact candidate commit and command bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from typing import Any

from agreement_contract import WITNESS_SCHEMA

from factory_runtime.host_git import HostGitError, materialize_commit, resolve_commit_tree

MAX_COMMAND_BYTES = 256 * 1024
MAX_PATCH_BYTES = 32 * 1024 * 1024
MAX_SELECTED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_CANDIDATE_FILES = 200_000
# Probe commands run offline against a pre-fetched dependency store; these name where it is. They
# are paths, not credentials, and pass through only when the caller set them (dogfood #58).
DEPENDENCY_CACHE_ENV = ("DENO_DIR", "npm_config_cache", "CARGO_HOME", "GOMODCACHE")


class ProbeError(RuntimeError):
    """The requested probe is unsafe, stale, vacuous, or did not discriminate."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def address(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def read_regular(path: pathlib.Path, *, ceiling: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > ceiling:
            raise ProbeError(f"input is not a bounded regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, ceiling + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > ceiling:
                raise ProbeError(f"input exceeds its byte ceiling: {path}")
        after = os.fstat(descriptor)
        installed = os.lstat(path)

        def identity(row: os.stat_result) -> tuple[int, int, int, int]:
            return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns

        if (
            identity(before) != identity(after)
            or stat.S_ISLNK(installed.st_mode)
            or (installed.st_dev, installed.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise ProbeError(f"input changed while read: {path}")
        return b"".join(chunks)
    except OSError as exc:
        raise ProbeError(f"cannot read input {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def safe_relative(value: str, label: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ProbeError(f"{label} must be a closed repository-relative path")
    return path


def load_command(path: pathlib.Path, label: str) -> tuple[list[str], str]:
    raw = read_regular(path, ceiling=MAX_COMMAND_BYTES)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"{label} is not JSON: {exc}") from exc
    if raw != canonical(value):
        raise ProbeError(f"{label} must be canonical JSON")
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 256
        or any(not isinstance(item, str) or not item or "\x00" in item for item in value)
    ):
        raise ProbeError(f"{label} must be a bounded non-empty argv array")
    return value, address(raw)


def extract_candidate(repo: pathlib.Path, candidate_sha: str, destination: pathlib.Path) -> None:
    """Write exactly the candidate commit's regular files from raw objects.

    The candidate repository is agent-written; ``git archive`` there would run its smudge
    filters and honor its ``export-ignore`` and replace refs (factory_runtime/host_git.py)."""

    try:
        materialize_commit(
            repo,
            candidate_sha,
            destination,
            admit_symlinks=False,
            max_files=MAX_CANDIDATE_FILES,
            max_total_bytes=MAX_ARCHIVE_BYTES,
        )
    except HostGitError as exc:
        raise ProbeError(f"cannot extract candidate: {exc}") from exc


def file_manifest(root: pathlib.Path, paths: Sequence[str]) -> tuple[str, int]:
    if list(paths) != sorted(set(paths)) or not paths:
        raise ProbeError("selected paths must be a sorted, unique, non-empty list")
    records: list[dict[str, Any]] = []
    total = 0
    for supplied in paths:
        relative = safe_relative(supplied, "selected path")
        selected = root.joinpath(*relative.parts)
        if not selected.exists() and not selected.is_symlink():
            raise ProbeError(f"selected path is absent: {supplied}")
        candidates = [selected]
        if selected.is_dir():
            candidates = sorted(path for path in selected.rglob("*") if not path.is_dir())
            if not candidates:
                raise ProbeError(f"selected directory contains no files: {supplied}")
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                raise ProbeError(f"selected surface contains a non-regular file: {candidate}")
            raw = read_regular(candidate, ceiling=MAX_SELECTED_BYTES)
            total += len(raw)
            if total > MAX_SELECTED_BYTES:
                raise ProbeError("selected surfaces exceed their aggregate byte ceiling")
            records.append(
                {
                    "path": candidate.relative_to(root).as_posix(),
                    "mode": stat.S_IMODE(candidate.stat().st_mode),
                    "digest": address(raw),
                }
            )
    if len({record["path"] for record in records}) != len(records):
        raise ProbeError("selected path roots overlap")
    return address(canonical(records)), total


def run_command(
    command: Sequence[str],
    *,
    cwd: pathlib.Path,
    timeout: int,
    label: str,
) -> int:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "CI": "1",
    }
    environment.update(
        {name: os.environ[name] for name in DEPENDENCY_CACHE_ENV if name in os.environ}
    )
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"{label} could not complete: {exc}") from exc
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise ProbeError(f"{label} output exceeded its byte ceiling")
    if completed.returncode < 0:
        raise ProbeError(f"{label} died from signal {-completed.returncode}")
    return completed.returncode


def install_receipt(root: pathlib.Path, body: dict[str, Any]) -> tuple[str, str]:
    raw = canonical(body)
    digest = address(raw)
    directory = root / "evidence" / "agreement" / "witnesses"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest.removeprefix('sha256:')}.json"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError as exc:
        if read_regular(path, ceiling=MAX_COMMAND_BYTES) != raw:
            raise ProbeError("content-addressed witness path contains different bytes") from exc
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return path.relative_to(root).as_posix(), digest


def probe(arguments: argparse.Namespace) -> dict[str, Any]:
    repo = arguments.candidate.resolve(strict=True)
    root = arguments.root.resolve(strict=True)
    if arguments.candidate.is_symlink() or not repo.is_dir():
        raise ProbeError("candidate repository must be a non-symlink directory")
    if not arguments.candidate_sha or not all(
        char in "0123456789abcdef" for char in arguments.candidate_sha
    ):
        raise ProbeError("candidate SHA must be a lowercase hexadecimal object id")
    try:
        resolve_commit_tree(repo, arguments.candidate_sha)
    except HostGitError as exc:
        raise ProbeError(
            f"candidate SHA does not resolve exactly in the candidate repository: {exc}"
        ) from exc
    local_command, local_command_digest = load_command(arguments.local_command, "local command")
    agreement_command, agreement_command_digest = load_command(
        arguments.agreement_command, "agreement command"
    )
    if local_command_digest == agreement_command_digest:
        raise ProbeError("local and agreement commands must be distinct")
    patch_raw = read_regular(arguments.mutation_patch, ceiling=MAX_PATCH_BYTES)
    patch_digest = address(patch_raw)
    with tempfile.TemporaryDirectory(prefix="factory-agreement-probe-") as temporary:
        temporary_root = pathlib.Path(temporary)
        frozen_patch = temporary_root / "mutation.patch"
        frozen_patch.write_bytes(patch_raw)
        workspace = temporary_root / "candidate"
        extract_candidate(repo, arguments.candidate_sha, workspace)
        candidate_paths = sorted(
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
        )
        if not candidate_paths:
            raise ProbeError("candidate archive is empty")
        local_digest, _local_bytes = file_manifest(workspace, arguments.local_suite)
        oracle_digest, _oracle_bytes = file_manifest(workspace, arguments.agreement_oracle)
        baseline_tree = file_manifest(workspace, candidate_paths)[0]
        baseline_local = run_command(
            local_command, cwd=workspace, timeout=arguments.timeout, label="baseline local suite"
        )
        baseline_agreement = run_command(
            agreement_command,
            cwd=workspace,
            timeout=arguments.timeout,
            label="baseline agreement oracle",
        )
        if baseline_local != 0 or baseline_agreement != 0:
            raise ProbeError("both local suite and agreement oracle must be green before mutation")
        if file_manifest(workspace, candidate_paths)[0] != baseline_tree:
            raise ProbeError("baseline commands changed a candidate-owned file")
        check = subprocess.run(
            [
                "git",
                "apply",
                "--no-index",
                "--check",
                str(frozen_patch),
            ],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        if check.returncode != 0:
            detail = check.stderr.decode("utf-8", errors="replace")[-2000:]
            raise ProbeError(
                f"mutation patch does not apply cleanly to the exact candidate: {detail}"
            )
        applied = subprocess.run(
            [
                "git",
                "apply",
                "--no-index",
                str(frozen_patch),
            ],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        mutated_tree = file_manifest(workspace, candidate_paths)[0]
        if applied.returncode != 0 or mutated_tree == baseline_tree:
            raise ProbeError("mutation patch did not produce a candidate change")
        if file_manifest(workspace, arguments.local_suite)[0] != local_digest:
            raise ProbeError("mutation changed the selected local suite")
        if file_manifest(workspace, arguments.agreement_oracle)[0] != oracle_digest:
            raise ProbeError("mutation changed the selected agreement oracle")
        mutated_local = run_command(
            local_command, cwd=workspace, timeout=arguments.timeout, label="mutated local suite"
        )
        mutated_agreement = run_command(
            agreement_command,
            cwd=workspace,
            timeout=arguments.timeout,
            label="mutated agreement oracle",
        )
        if file_manifest(workspace, candidate_paths)[0] != mutated_tree:
            raise ProbeError("mutated commands changed a candidate-owned file")
        if file_manifest(workspace, arguments.local_suite)[0] != local_digest:
            raise ProbeError("mutated commands changed the selected local suite")
        if file_manifest(workspace, arguments.agreement_oracle)[0] != oracle_digest:
            raise ProbeError("mutated commands changed the selected agreement oracle")
    if mutated_local != 0 or mutated_agreement == 0:
        raise ProbeError("mutation must leave the local suite green and turn agreement red")
    body = {
        "schema_version": WITNESS_SCHEMA,
        "run_id": arguments.run_id,
        "requirement_id": arguments.requirement_id,
        "direction": arguments.direction,
        "candidate_sha": arguments.candidate_sha,
        "local_suite_digest": local_digest,
        "agreement_oracle_digest": oracle_digest,
        "patch_digest": patch_digest,
        "local_command_digest": local_command_digest,
        "agreement_command_digest": agreement_command_digest,
        "baseline_local_exit": baseline_local,
        "baseline_agreement_exit": baseline_agreement,
        "mutated_local_exit": mutated_local,
        "mutated_agreement_exit": mutated_agreement,
        "witnessed": True,
    }
    path, digest = install_receipt(root, body)
    return {
        "schema_version": WITNESS_SCHEMA,
        "witness": {"path": path, "digest": digest},
        "local_suite_digest": local_digest,
        "agreement_oracle_digest": oracle_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--requirement-id", required=True)
    parser.add_argument("--direction", choices=("producer", "consumer"), required=True)
    parser.add_argument("--candidate", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--mutation-patch", type=pathlib.Path, required=True)
    parser.add_argument("--local-command", type=pathlib.Path, required=True)
    parser.add_argument("--agreement-command", type=pathlib.Path, required=True)
    parser.add_argument("--local-suite", action="append", required=True)
    parser.add_argument("--agreement-oracle", action="append", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    arguments = parser.parse_args()
    if not 1 <= arguments.timeout <= 3600:
        parser.error("--timeout must be between 1 and 3600 seconds")
    try:
        print(json.dumps(probe(arguments), sort_keys=True))
    except (ProbeError, OSError) as exc:
        print(f"agreement probe refused: {exc}", file=sys.stderr)
        return 71
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
