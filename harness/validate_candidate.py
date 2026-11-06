"""Run the Validator's chosen checks against one exact committed candidate.

A test result is evidence for the Validator, not a signature or automatic product verdict.
No host Git command executes hooks, filters, or replacement objects from an author repository.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import importlib
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from factory_runtime.host_git import materialize_commit, run_host_git

_record = (importlib.import_module("harness.run_record") if __package__
           else importlib.import_module("run_record"))
load, write_new = _record.load, _record.write_new


def retain_result(root: Path, result_root: Path, result: dict[str, Any]) -> None:
    """Retain raw evidence before notifying the existing independent reviewer.

    Command success is never product acceptance. A failed inbox write preserves the
    result and fails visibly; a queued notice does not attest that review happened.
    """
    result["acceptance"] = "not-evaluated"
    path = result_root / "result.json"
    write_new(path, (json.dumps(result, indent=2) + "\n").encode())
    candidate = result.get("candidate", {}).get("commit", result["requested_commit"])
    notice = (
        f"Validation evidence for run {result['run']}, candidate {candidate}: "
        f"{result['status']} (exit {result['exit_code']}). "
        f"Result: {path}. Output: {result_root / 'output.log'}. "
        "Acceptance is not-evaluated. Continue the regular code and test quality check-ins; "
        "Do not substitute endgame review for earlier check-ins. Review actual code, tests "
        "and behavioral evidence "
        "before accepting the affected claims. Internal-document/comment wording checks, "
        "duplicate assertions and tests of doubles do not prove the promised behavior. "
        "Inspect observed negative and positive controls; raise concrete findings to the "
        "Validator, including architecture, maintainability and proportionate operability gaps. "
        "Drop unproductive arguments immediately; escalate persistent corrections or disputed "
        "importance after three to four substantive rounds without requiring consensus. "
        "This notice being queued does not mean a review occurred."
    )
    try:
        _record.message(root, "orchestrator", "host", notice)
    except (ValueError, OSError, RuntimeError) as error:
        raise ValueError(
            f"validation evidence retained at {path}; Orchestrator review notice "
            f"could not be queued: {error}"
        ) from error


def validate(
    runs: Path,
    run: str,
    repository: Path,
    commit: str,
    command: list[str],
    *,
    timeout: float | None = None,
    lfs_objects: Path | None = None,
    require_lfs: bool = False,
) -> tuple[int, Path]:
    root, task = load(runs, run)
    if not command:
        raise ValueError("supply the actual project test command after --")
    if timeout is not None and timeout <= 0:
        raise ValueError("timeout must be positive")
    parent = root / "validation"
    if parent.is_symlink():
        raise ValueError("validation directory is a symlink")
    parent.mkdir(mode=0o700, exist_ok=True)
    result_root = parent / uuid.uuid4().hex
    result_root.mkdir(mode=0o700)
    candidate = result_root / "candidate"
    result: dict[str, Any] = {
        "run": run,
        "requested_commit": commit,
        "repository": str(repository.resolve()),
        "command": command,
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    try:
        # Candidate clones often share assets with the original local checkout.
        # Read only local stores; never invoke git-lfs, fetch, hooks or smudge filters.
        if lfs_objects is None:
            for source in dict.fromkeys((repository.resolve(), Path(task["repository"]))):
                common = Path(run_host_git(
                    source, ["rev-parse", "--path-format=absolute", "--git-common-dir"],
                ).decode().strip())
                local = common / "lfs" / "objects"
                if local.is_dir():
                    lfs_objects = local
                    break
        result["lfs"] = {"required": require_lfs,
                         "objects": str(lfs_objects.resolve()) if lfs_objects else None}
        materialized = materialize_commit(
            repository,
            commit,
            candidate,
            admit_symlinks=True,
            contained_file_links=True,
            max_files=1000000,
            max_total_bytes=16 * 1024**3,
            lfs_objects=lfs_objects,
            max_lfs_bytes=16 * 1024**3 if lfs_objects is not None else None,
        )
        result["candidate"] = dataclasses.asdict(materialized)
        if require_lfs and materialized.lfs_pointers_left:
            raise ValueError(
                f"candidate requires LFS content; {materialized.lfs_pointers_left} "
                "pointer file(s) remain. Supply a complete local store with --lfs-objects."
            )
    except (ValueError, OSError, RuntimeError) as error:
        result.update(
            exit_code=70, status="validation-failed", error=str(error),
            finished_at=dt.datetime.now(dt.UTC).isoformat(),
        )
        retain_result(root, result_root, result)
        return 70, result_root
    env = {name: os.environ[name] for name in ("PATH", "LANG", "TMPDIR") if name in os.environ}
    env.update(HOME=str(result_root), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    with (result_root / "output.log").open("xb") as stream:
        try:
            child = subprocess.Popen(
                command,
                cwd=candidate,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # This group was created for this exact test command; no name-based cleanup.
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                code = 124
                result["error"] = "test command timed out"
        except OSError as error:
            code = 127
            result["error"] = str(error)
    result.update(
        exit_code=code,
        status="tests-passed" if code == 0 else "tests-failed",
        finished_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    retain_result(root, result_root, result)
    return code, result_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("commit")
    parser.add_argument(
        "--runs", type=Path, default=Path(os.environ.get("FACTORY_RUNS_DIR", ".factory/runs"))
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--lfs-objects", type=Path,
                        help="local LFS object store (otherwise discover candidate/source store)")
    parser.add_argument("--require-lfs", action="store_true",
                        help="refuse to run checks while any pointer files remain")
    # Parse the command separately so project flags never become Factory options.
    argv = sys.argv[1:]
    if "--" not in argv:
        parser.error("supply a project test command after --")
    split = argv.index("--")
    args = parser.parse_args(argv[:split])
    try:
        code, result = validate(
            args.runs, args.run, args.repo, args.commit, argv[split + 1 :], timeout=args.timeout,
            lfs_objects=args.lfs_objects, require_lfs=args.require_lfs
        )
        evidence = json.loads((result / "result.json").read_text())
        if "candidate" in evidence:
            candidate = evidence["candidate"]
            print(f"LFS: hydrated {candidate['lfs_hydrated']} file(s), "
                  f"{candidate['lfs_bytes']} bytes; "
                  f"{candidate['lfs_pointers_left']} pointer file(s) remain")
        print(f"{'PASS' if code == 0 else 'FAIL'}: checks only; "
              f"product acceptance not evaluated; {result}/result.json")
        print("Product acceptance: not evaluated. Orchestrator review notice queued.")
        return 0 if code == 0 else 1
    except (ValueError, OSError, RuntimeError) as error:
        print(f"validation: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
