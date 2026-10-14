#!/usr/bin/env python3
"""discovery_check — the integrated tree's test runner finds every test the Tester froze.

The invariant: **no verdict is rendered on a candidate whose runner cannot see the Tester's
suite.** In run c the Coder's projection hid tests/, so the Coder excluded tests/ in deno.json;
the integrated tree's ``deno test`` then reported "No test modules found" at Phase C, and the
shipped repository's own test task would have too (dogfood #42). After both lanes are frozen and
the tree is integrated, this runs an inventory command in that tree and refuses when it exits
non-zero, prints anything but one ``factory-test-inventory/1`` document (``test_inventory.py``
documents the format and holds every runner-specific parse), or when any test module in the
Tester's latest freeze has no runnable collected test in it. Console lines are never read: a
diagnostic that names a module is not an inventory. A skipped or ignored test is not runnable:
it cannot fail, so it covers nothing.

It also checks the planned tests (dogfood #73): every Critical ``planned-case`` id in
``--strategy`` must appear in the name of a runnable collected test from one of the Tester's
modules. A module that only mentions the id (a constant, a comment) has no test that carries it.

  discovery_check.py --root <run> --tree <integrated tree> --candidate-sha <commit>
                     --pattern <glob> --strategy <md> [--slot tester] [--timeout-minutes 10]
                     -- <inventory command, e.g. python3 harness/test_inventory.py deno>

``--tree`` must be a Git checkout whose HEAD is exactly ``--candidate-sha`` with no tracked or
untracked change (ignored files such as installed dependencies are allowed), read through
``host_git`` so nothing the tree planted runs. ``--pattern`` names the Tester's test modules
inside its frozen export (``fnmatch``, where ``*`` also crosses ``/``: ``tests/*_test.ts``). A
pattern that matches nothing is refused, so the check cannot pass vacuously. Exit 0 writes and
prints a ``factory-discovery-check/1`` receipt bound to the candidate commit, the sha256 of the
Strategy bytes it read and the sha256 of the Tester's latest freeze record; exit 70 names what
the runner missed: a configuration conflict to route as an integration edit, never a reason to
skip the suite.

  discovery_check.py require --root <run> --candidate-sha <commit> --strategy <md>

is how ``endgame.sh`` enforces the check before any verdict: it refuses (exit 70) unless every
Tester slot that froze in this run has a receipt for exactly that commit, the ratified Strategy's
current bytes (its ``.digest`` sidecar present) and that slot's latest freeze, with no Critical
planned case untested. A Strategy that declares ``test-modules`` requires a receipt even when no
Tester slot froze, so the requirement cannot be dropped by skipping the freeze.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

_HARNESS_ROOT = pathlib.Path(__file__).resolve().parent
for _path in (str(_HARNESS_ROOT), str(_HARNESS_ROOT.parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import strategy_plan  # noqa: E402

from factory_runtime.host_git import HostGitError, run_host_git  # noqa: E402

FREEZE_SCHEMA = "factory-tmux-lane-freeze/1"
INVENTORY_SCHEMA = "factory-test-inventory/1"
RECEIPT_SCHEMA = "factory-discovery-check/1"
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SLOT = re.compile(r"^tester(?:-[a-z0-9][a-z0-9-]{0,15})?$")


class DiscoveryError(RuntimeError):
    pass


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def latest_freeze(root: pathlib.Path, slot: str) -> dict[str, Any] | None:
    """The slot's latest freeze record, None when it never froze; refuses an unfrozen reopen."""

    journal = root / "tmux-lanes" / f"{slot}-launch.jsonl"
    try:
        rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        raise DiscoveryError(f"no readable launch journal for {slot}: {error}") from None
    freezes = [row for row in rows if isinstance(row, dict)
               and row.get("schema_version") == FREEZE_SCHEMA]
    if not freezes:
        return None
    if rows[-1] is not freezes[-1]:
        raise DiscoveryError(f"{slot} is not frozen; freeze both lanes before the check")
    return freezes[-1]


def _freeze_digest(freeze: dict[str, Any]) -> str:
    return _sha256(json.dumps(freeze, sort_keys=True, separators=(",", ":")).encode())


def frozen_freeze(root: pathlib.Path, slot: str) -> dict[str, Any]:
    freeze = latest_freeze(root, slot)
    if freeze is None:
        raise DiscoveryError(f"{slot} is not frozen; freeze both lanes before the check")
    return freeze


def test_modules(files: pathlib.Path, pattern: str) -> list[str]:
    modules = sorted(path.relative_to(files).as_posix() for path in files.rglob("*")
                     if path.is_file() and fnmatch.fnmatch(path.relative_to(files).as_posix(),
                                                          pattern))
    if not modules:
        raise DiscoveryError(f"--pattern {pattern!r} matches no file in the Tester's freeze")
    return modules


def _tests(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(test, dict) and set(test) == {"module", "name"}
        and all(isinstance(field, str) for field in test.values()) for test in value)


def inventory(output: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(runnable, skipped) tests of one ``factory-test-inventory/1`` document; else refuses."""

    try:
        document = json.loads(output)
    except json.JSONDecodeError:
        document = None
    if (not isinstance(document, dict) or document.get("schema_version") != INVENTORY_SCHEMA
            or not _tests(document.get("tests")) or not _tests(document.get("skipped", []))):
        raise DiscoveryError(f"the inventory command did not print one {INVENTORY_SCHEMA} "
                             f"document: {output.strip()[:200]!r}")
    return document["tests"], document.get("skipped", [])


def collected(tree: pathlib.Path, command: list[str],
              timeout_s: float) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    try:
        completed = subprocess.run(command, cwd=tree, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=timeout_s,
                                   check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DiscoveryError(f"the inventory command did not complete: {error}") from None
    if completed.returncode != 0:
        raise DiscoveryError(f"the inventory command exited {completed.returncode}: "
                             f"{(completed.stderr or completed.stdout).strip()[-400:]}")
    return inventory(completed.stdout)


def candidate_tree(tree: pathlib.Path, candidate: str) -> None:
    """Refuse unless ``tree`` is exactly ``candidate``'s checkout, unchanged."""

    if not _COMMIT.fullmatch(candidate):
        raise DiscoveryError("--candidate-sha must be an exact object id")
    try:
        head = run_host_git(tree, ["rev-parse", "--verify", "HEAD^{commit}"]).decode().strip()
        status = run_host_git(tree, ["status", "--porcelain", "--untracked-files=all"])
    except HostGitError as error:
        raise DiscoveryError(f"the integrated tree is not a readable Git checkout: {error}") \
            from None
    if head != candidate:
        raise DiscoveryError(f"the integrated tree is at {head}, not candidate {candidate}; "
                             "check the exact commit endgame will judge")
    if status.strip():
        raise DiscoveryError("the integrated tree has uncommitted or untracked changes, so it is "
                             "not the candidate commit; commit them or remove them")


def strategy_bytes(path: pathlib.Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DiscoveryError(f"the testing strategy is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise DiscoveryError(f"the testing strategy cannot be read: {error}") from None


def strategy_lines(raw: bytes) -> list[str]:
    try:
        return raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise DiscoveryError("the testing strategy is not UTF-8") from None


def receipt_path(root: pathlib.Path, slot: str, candidate: str) -> pathlib.Path:
    return root / "discovery" / f"{slot}-{candidate}.json"


def _write_receipt(path: pathlib.Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as pending:
        pending.write(raw)
        pending.flush()
        os.fsync(pending.fileno())
    os.replace(pending.name, path)


def check(arguments: argparse.Namespace, command: list[str]) -> dict[str, Any]:
    """The receipt for one green discovery check; DiscoveryError names what is missing."""

    raw_strategy = strategy_bytes(arguments.strategy)
    try:
        cases = strategy_plan.planned_cases(strategy_lines(raw_strategy))
    except strategy_plan.PlanError as error:
        raise DiscoveryError(str(error)) from None
    freeze = frozen_freeze(arguments.root, arguments.slot)
    files = pathlib.Path(str(freeze.get("export", {}).get("files_directory", "")))
    modules = test_modules(files, arguments.pattern)
    candidate_tree(arguments.tree, arguments.candidate_sha)
    tests, skipped = collected(arguments.tree, command, arguments.timeout_minutes * 60)
    listed = {test["module"] for test in tests}
    missing = [module for module in modules if module not in listed]
    if missing:
        raise DiscoveryError(
            f"the runner lists no runnable test in {len(missing)} of the Tester's "
            f"{len(modules)} modules: {', '.join(missing)}. Fix the integrated tree's test "
            "configuration as an integration edit before Phase C (a module whose tests are all "
            "skipped or ignored covers nothing).")
    gaps = strategy_plan.untested(
        cases, [test["name"] for test in tests if test["module"] in modules])
    if gaps["Critical"]:
        only_skipped = [case for case in gaps["Critical"] if strategy_plan.names_case(
            case, "\n".join(test["name"] for test in skipped))]
        detail = (f" ({', '.join(only_skipped)} named only by skipped or ignored tests)"
                  if only_skipped else "")
        raise DiscoveryError(
            "no runnable collected test of the Tester's names these Critical planned cases: "
            f"{', '.join(gaps['Critical'])}{detail}. Send them back to the Tester.")
    return {"schema_version": RECEIPT_SCHEMA, "slot": arguments.slot,
            "candidate_sha": arguments.candidate_sha,
            "strategy_sha256": _sha256(raw_strategy),
            "freeze_sha256": _freeze_digest(freeze),
            "pattern": arguments.pattern, "command": command,
            "modules": modules, "untested": gaps,
            "skipped": sorted({f"{test['module']}::{test['name']}" for test in skipped})}


def frozen_tester_slots(root: pathlib.Path) -> list[str]:
    lanes = root / "tmux-lanes"
    slots = sorted(path.name.removesuffix("-launch.jsonl")
                   for path in lanes.glob("tester*-launch.jsonl")) if lanes.is_dir() else []
    return [slot for slot in slots if _SLOT.fullmatch(slot) and latest_freeze(root, slot)]


def require(root: pathlib.Path, candidate: str, strategy: pathlib.Path) -> list[str]:
    """Slots whose discovery receipt binds ``candidate``; DiscoveryError when one does not."""

    if not _COMMIT.fullmatch(candidate):
        raise DiscoveryError("--candidate-sha must be an exact object id")
    slots = frozen_tester_slots(root)
    raw_strategy = strategy_bytes(strategy) if slots or strategy.exists() else b""
    declares_modules = bool(strategy_plan.test_module_globs(strategy_lines(raw_strategy)))
    if not slots and declares_modules:
        raise DiscoveryError("the Strategy declares test-modules but no Tester slot froze in "
                             "this run, so discovery of its tests cannot be proven")
    if not slots:
        return []
    sidecar = strategy.with_name(strategy.name + ".digest")
    if sidecar.is_symlink() or not sidecar.is_file() or not sidecar.stat().st_size:
        raise DiscoveryError(f"the testing strategy is not ratified: no {sidecar.name}")
    expected = {"candidate_sha": candidate, "strategy_sha256": _sha256(raw_strategy)}
    for slot in slots:
        path = receipt_path(root, slot, candidate)
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise DiscoveryError(f"no discovery receipt for {slot} at candidate {candidate}; "
                                 "run discovery_check.py on the integrated tree first") from None
        stale = [field for field, value in {
            **expected, "schema_version": RECEIPT_SCHEMA, "slot": slot,
            "freeze_sha256": _freeze_digest(frozen_freeze(root, slot)),
        }.items() if not isinstance(receipt, dict) or receipt.get(field) != value]
        untested = receipt.get("untested") if isinstance(receipt, dict) else None
        if not stale and (not isinstance(untested, dict) or untested.get("Critical") != []):
            stale = ["untested"]
        if stale:
            raise DiscoveryError(f"the discovery receipt for {slot} does not bind this candidate, "
                                 f"the ratified Strategy and the latest freeze ({', '.join(stale)}"
                                 "); re-run discovery_check.py")
    return slots


def _require_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="a verdict needs a discovery receipt")
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--strategy", type=pathlib.Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        slots = require(arguments.root, arguments.candidate_sha, arguments.strategy)
    except (DiscoveryError, strategy_plan.PlanError) as error:
        print(f"discovery-check: {error}", file=sys.stderr)
        return 70
    print(json.dumps({"required": bool(slots), "slots": slots}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["require"]:
        return _require_main(argv[1:])
    parser = argparse.ArgumentParser(description="the runner discovers the Tester's modules")
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--tree", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--strategy", type=pathlib.Path, required=True)
    parser.add_argument("--slot", default="tester")
    parser.add_argument("--timeout-minutes", type=float, default=10.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("needs the inventory command after --")
    if not _SLOT.fullmatch(arguments.slot):
        parser.error("--slot must be tester or tester-<instance>")
    path = receipt_path(arguments.root, arguments.slot, arguments.candidate_sha)
    try:
        receipt = check(arguments, command)
    except DiscoveryError as error:
        # A failed check supersedes any earlier green receipt for this candidate and slot.
        if _COMMIT.fullmatch(arguments.candidate_sha):
            path.unlink(missing_ok=True)
        print(f"discovery-check: {error}", file=sys.stderr)
        return 70
    _write_receipt(path, receipt)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
