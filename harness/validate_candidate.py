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

from factory_runtime.host_git import HostGitError, materialize_commit, run_host_git
from factory_runtime.kindex_workspace import mark_no_index

_record = (importlib.import_module("harness.run_record") if __package__
           else importlib.import_module("run_record"))
load, write_new = _record.load, _record.write_new
_native = (importlib.import_module("harness.native_session") if __package__
           else importlib.import_module("native_session"))


class BaseUnavailable(RuntimeError):
    """The run's base commit could not be read, so what it held is not known.

    Not knowing is a different fact from the base holding nothing, and is never reported
    as that: a candidate is judged against the base or not judged at all.
    """


def _base_entry(task: dict[str, Any], relative: str) -> tuple[str, bytes] | None:
    """What the run's base commit holds at ``relative``: ("link", its target),
    ("file", its bytes) or, for anything that is not a file, (its Git type, b"").

    None means one thing only: Git read the base commit and listed nothing at that path.
    A Git command that fails, times out or answers in a form this cannot read raises
    ``BaseUnavailable``.
    """

    source, commit = Path(task["repository"]), task["resolved_commit"]
    try:
        listing = run_host_git(source, ["ls-tree", "-z", commit, "--", relative])
        if not listing:
            return None
        fields = listing.split(b"\t", 1)[0].split()
        if len(fields) != 3:
            raise BaseUnavailable(
                f"the run's base commit {commit} gave an unreadable listing for {relative}")
        mode, kind, name = (field.decode("ascii", errors="replace") for field in fields)
        if kind != "blob":
            return (kind, b"")
        content = run_host_git(source, ["cat-file", "blob", name])
    except HostGitError as error:
        raise BaseUnavailable(
            f"the run's base commit {commit} could not be read for {relative}, so whether "
            f"the target already had that path is not known: {error}") from error
    return ("link" if mode == "120000" else "file", content)


def lane_project_files(root: Path, task: dict[str, Any], candidate: Path,
                       repository: Path | None = None) -> list[str]:
    """The lane runtime files a candidate carries, each with why it is one.

    A Cursor lane writes its hooks, its Kindex and one permission rule into its clone's
    ``.cursor/``, a Muse lane writes its hooks into ``.muse/hooks.json``, and both keep them
    out of ordinary commits, but an author can force one in. A candidate is the work, not
    the lane's configuration, so such a commit is not validated. Each provider's paths are
    judged against lanes of that provider: "the run launched a Cursor lane" below reads, for
    the Muse path, "the run launched a Muse lane".

    The run's base commit is consulted first. A file that is byte-for-byte what the target
    held at that commit is the target's own, whatever it says and whatever ran, and is
    never refused: a project may legitimately track Cursor configuration that mentions the
    same words a lane's file does. Only a file that is new or changed since the base is
    judged. It is the lane's when it is byte-for-byte what a lane's manifest recorded, or
    when the run launched a Cursor lane (a lane never starts where one of these paths is
    already the target's, so in such a run any difference is the lane's). A file the base
    did not have at all is also the lane's when it holds the lane's own hook command or
    server name. A file the target already had and has since changed, in a run with no
    Cursor lane, is the target's own work on its own file and is left alone.

    A link at one of the paths is compared the same way, as a link: unchanged from the
    base it is the target's own; new or changed in a run that launched a Cursor lane it is
    refused. It is never followed, so what it points at is not read. The same holds for a
    link anywhere on the way to one of the paths (``.agents``, ``.agents/plugins``, the
    plugin directory, ``.cursor``, ``.muse``): the path beneath it is not looked at through
    it, an unchanged link is the target's own, and a new or changed one is refused when a
    lane of that provider ran.

    Lane manifests are read from the run's control tree and, when ``repository`` is the
    lane's own clone, from its private runtime under ``.git/factory-runtime``, which is
    where a lane under a kernel boundary keeps them.

    The base is read only for a path the candidate actually has. When it cannot be read
    (Git fails or times out) ``BaseUnavailable`` is raised: the candidate is not treated as
    though the target had nothing there, and it is not refused as a lane's either.

    Scope, exactly: the three paths a Cursor lane writes (``.cursor/hooks.json``,
    ``.cursor/mcp.json``, ``.cursor/cli.json``) and the one a Muse lane writes
    (``.muse/hooks.json``). A copy of a lane's file committed under another name or
    elsewhere in the tree is not looked for.
    """

    lanes = root / "tmux-lanes"
    usable = lanes.is_dir() and not lanes.is_symlink()
    manifests = sorted(lanes.glob("*-agent-home/*-project-files.json")) if usable else []
    journals = sorted(lanes.glob("*-launch.jsonl")) if usable else []
    if repository is not None:
        runtime = repository / ".git" / "factory-runtime"
        if runtime.is_dir() and not runtime.is_symlink() \
                and not (repository / ".git").is_symlink():
            manifests += sorted(runtime.glob("*/agent/*-project-files.json"))
    manifests = [manifest for manifest in manifests if not manifest.is_symlink()]
    ran: set[str] = set()  # the agents whose lanes this run launched
    for journal in journals:
        if journal.is_symlink():
            continue
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("agent") in _native.PROJECT_FILES:
                ran.add(row["agent"])
    found: list[str] = []
    owners = [(relative, agent) for agent, relatives in _native.PROJECT_FILES.items()
              for relative in relatives]
    for relative, agent in owners:
        label = _native.PROJECT_LABEL[agent]
        label = ("an " if label[0] in "AEIOU" else "a ") + label
        lane_ran = agent in ran
        # Outermost first: a link on the way is judged as a link and never followed, so
        # nothing outside the materialized candidate is read through it.
        parts = Path(relative).parts
        on_the_way = next((str(Path(*parts[:depth])) for depth in range(1, len(parts))
                           if (candidate / Path(*parts[:depth])).is_symlink()), None)
        if on_the_way is not None:
            link = os.readlink(candidate / on_the_way).encode()
            if _base_entry(task, on_the_way) != ("link", link) and lane_ran:
                entry = (f"{on_the_way} ({label} lane ran in this run and this link, on "
                         f"the way to a lane's files, is new or changed since the run's "
                         "base commit)")
                if entry not in found:
                    found.append(entry)
            continue
        path = candidate / relative
        if not path.is_symlink() and not path.is_file():
            continue
        base = _base_entry(task, relative)
        if path.is_symlink():
            # A link is judged as a link and never followed.
            if base == ("link", os.readlink(path).encode()):
                continue  # the target's own link, exactly as it was before the run
            reason = (f"{label} lane ran in this run and the link is new or changed since "
                      "the run's base commit") if lane_ran else ""
            if reason:
                found.append(f"{relative} ({reason})")
            continue
        data = path.read_bytes()
        if base == ("file", data):
            continue  # the target's own file, exactly as it was before the run
        # What the file says counts only where the target had no such file: words alone do
        # not make the target's own file a lane's. A recorded digest counts either way.
        reason = _native.lane_file_in_candidate(
            relative, data, manifests, marks=base is None)
        if not reason and lane_ran:
            reason = (f"{label} lane ran in this run and the file is new or changed since "
                      "the run's base commit")
        if reason:
            found.append(f"{relative} ({reason})")
    return found


def retain_result(root: Path, result_root: Path, result: dict[str, Any]) -> None:
    """Retain raw evidence before notifying the existing independent reviewer.

    Command success is never product acceptance. A failed inbox write preserves the
    result and fails visibly; a queued notice does not attest that review happened.
    """
    result["acceptance"] = "not-evaluated"
    if "candidate" in result:
        _, task = load(root.parent, result["run"])
        try:
            result["builder_provenance"] = _record.check_builder(
                root, task, Path(result["repository"]), result["candidate"]["commit"])
        except (ValueError, OSError, RuntimeError) as error:
            result["builder_provenance"] = dict(status="unknown", findings=[str(error)])
    provenance = result.setdefault("builder_provenance", dict(status="unknown", findings=[]))
    provenance.setdefault("pr_verification", "not-checked")
    provenance["system_one"] = _record.system_one_review(provenance)
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
        "This notice being queued does not mean a review occurred. "
        f"Builder provenance: {provenance['status']}. "
        f"PR verification: {provenance['pr_verification']}. "
        f"System One: {provenance['system_one']['status']} "
        f"({provenance['system_one'].get('reason', 'see retained observation')}). "
        f"Retained builder: {json.dumps(provenance.get('expected', {}).get('identity', {}))}. "
        "Check the actual PR head/body with run_record.py provenance before publication."
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
    mark_no_index(result_root)
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
            contained_directory_links=True,
            max_files=1000000,
            max_total_bytes=16 * 1024**3,
            lfs_objects=lfs_objects,
            max_lfs_bytes=16 * 1024**3 if lfs_objects is not None else None,
        )
        result["candidate"] = dataclasses.asdict(materialized)
        carried = lane_project_files(root, task, candidate, repository.resolve())
        if carried:
            whose = " and ".join(
                _native.PROJECT_LABEL[agent] for agent, relatives in _native.PROJECT_FILES.items()
                if any(relative == item.split(" ", 1)[0]
                       or relative.startswith(item.split(" ", 1)[0] + "/")
                       for item in carried for relative in relatives))
            raise ValueError(
                f"candidate commit carries a lane's own {whose} project configuration, which "
                "is not part of the work: " + "; ".join(carried) + ". Take those paths out "
                "of the commit and validate the new commit.")
        if require_lfs and materialized.lfs_pointers_left:
            raise ValueError(
                f"candidate requires LFS content; {materialized.lfs_pointers_left} "
                "pointer file(s) remain. Supply a complete local store with --lfs-objects."
            )
    except (ValueError, OSError, RuntimeError) as error:
        # Evidence that could not be read is reported as that, not as a finding about the
        # candidate: nothing was established against it and no check was run.
        result.update(
            exit_code=70,
            status=("validation-unavailable" if isinstance(error, BaseUnavailable)
                    else "validation-failed"),
            error=str(error),
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
        provenance = evidence["builder_provenance"]
        print(f"PR verification: {provenance['pr_verification']}; "
              f"System One: {provenance['system_one']['status']} "
              f"({provenance['system_one'].get('reason', 'see retained observation')}).")
        print("Product acceptance: not evaluated. Orchestrator review notice queued.")
        return 0 if code == 0 else 1
    except (ValueError, OSError, RuntimeError) as error:
        print(f"validation: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
