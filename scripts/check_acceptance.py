#!/usr/bin/env python3
"""check_acceptance.py — the acceptance instrument's data-file checker (plan §0.3).

The remediation's binding criteria (net LOC, obligation counts, t_signal) must derive
from committed data and the git tree, never from hand-maintained numbers or ambient
environment. This checker verifies the two data files at build time:

- ``removal_ledger.jsonl`` — identity-only rows (gate id / path / symbol + kind), never a
  ``removed_loc`` figure: a hand-maintained LOC number next to a git-derived one is
  one-fact-two-authorities and guaranteed drift. LOC derives solely from
  ``git diff <pre_tag>..HEAD``. A landed ``delete`` row must name a path that existed at
  ``pre_tag`` and is absent at HEAD; a landed ``add`` row must name a path present at
  HEAD; a landed ``gate-retire`` row must name a gate absent from ``gates.tsv`` whose
  pre_tag probes no longer collect anywhere in the suite (dead verification code dies
  with its gate). Rows still ``planned`` make no tree claim — scoped verification, per
  the plan, until the remediation boundary commit.

- ``acceptance_baseline.json`` — carries ``pre_tag`` and the committed, EXHAUSTIVE
  NO-relevant classification: every kind registered in ``refusal_event_kinds.json`` and
  ``terminal_no_kinds.json`` must appear exactly once in ``no_relevant_kinds``, so a kind
  added to a registry without a classification fails the build (additions are git-visible
  data diffs, never runtime-invented kinds). Two pins are structural, not editorial:
  ``watchdog-deadline`` must be ``false`` (§0.2 — a deadline expiry is a BOUND; the
  deadline knob cannot manufacture the terminal event the instrument rewards) and
  ``blocking_written`` must be in ``excluded_event_kinds`` (§0.4 — a consumable
  coordination signal must not earn early-NO credit). Every baseline row cites a primary
  artifact (path + sha256, verified) or is an explicit UNDERIVED with a non-empty
  justification — a number with neither is invented.

Ambient overrides are refused on PRESENCE (the check_denial_probes.py pattern): a checker
whose inputs the caller's environment can substitute is not a gate. The explicit
``--ledger`` / ``--baseline`` / ``--repo`` argv seam exists for tests only.

Exit codes: 0 green; 1 a claim failed; 2 the inputs could not be read or an ambient
override was present (fail-closed — the build cannot verify, so it does not ship).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AMBIENT_OVERRIDES = ("REMOVAL_LEDGER", "ACCEPTANCE_BASELINE", "ACCEPTANCE_PRE_TAG")
LEDGER_KINDS = {"add", "delete", "de-rate", "demote", "gate-retire"}
LEDGER_STATUSES = {"planned", "landed"}
FORBIDDEN_LEDGER_FIELDS = {"removed_loc", "loc", "lines"}


def _die(message: str, code: int = 2) -> None:
    print(f"check-acceptance: {message}", file=sys.stderr)
    raise SystemExit(code)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def _exists_at_tag(repo: Path, tag: str, path: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{tag}:{path}").returncode == 0


def _load_registry_kinds(repo: Path) -> tuple[set[str], dict[str, str]]:
    """Return (all registered kinds, kind -> signal/bound class).

    Round-5 F-6: BOTH registries carry the class; the registry owns each
    kind's NO-relevance and the baseline only cites it. A registered kind
    with no class is itself a failure surfaced by the caller's iff check
    (an unclassed kind can never agree with any citation).
    """
    kinds: set[str] = set()
    classes: dict[str, str] = {}
    for name in ("refusal_event_kinds.json", "terminal_no_kinds.json"):
        try:
            document = json.loads((repo / "harness" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _die(f"kind registry unreadable: harness/{name}: {exc}")
        kinds.update(document["kinds"])
        for kind, spec in document["kinds"].items():
            if isinstance(spec, dict):
                classes[str(kind)] = str(spec.get("class", ""))
    return kinds, classes


def check(repo: Path, ledger_path: Path, baseline_path: Path) -> list[str]:
    failures: list[str] = []

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _die(f"baseline unreadable: {baseline_path}: {exc}")
    pre_tag = baseline.get("pre_tag")
    if not isinstance(pre_tag, str) or not pre_tag:
        _die("baseline has no pre_tag")
    resolved = _git(repo, "rev-parse", "--verify", f"{pre_tag}^{{commit}}")
    if resolved.returncode != 0:
        _die(f"pre_tag does not resolve to a commit: {pre_tag}")
    # Round-3 G2: the tag is pinned by COMMIT, not by name — `git tag -f` moving
    # remediation-pre must show up as a committed-data diff, never silently.
    pinned = baseline.get("pre_tag_commit")
    if not isinstance(pinned, str) or not pinned:
        failures.append("baseline has no pre_tag_commit — the boundary is movable by name")
    elif resolved.stdout.strip() != pinned:
        failures.append(
            f"pre_tag {pre_tag} resolves to {resolved.stdout.strip()[:12]} but the baseline "
            f"pins {pinned[:12]} — the boundary tag moved"
        )

    # --- the exhaustive NO-relevant kind classification -----------------------------
    registered, registry_classes = _load_registry_kinds(repo)
    classified = baseline.get("no_relevant_kinds")
    if not isinstance(classified, dict):
        _die("baseline has no no_relevant_kinds map")
    missing = registered - set(classified)
    extra = set(classified) - registered
    if missing:
        failures.append(f"kinds registered but unclassified: {sorted(missing)}")
    if extra:
        failures.append(f"kinds classified but not registered anywhere: {sorted(extra)}")
    for kind, value in classified.items():
        if not isinstance(value, bool):
            failures.append(f"no_relevant_kinds[{kind!r}] is not a boolean")
    if "watchdog-deadline" in registered and classified.get("watchdog-deadline") is not False:
        failures.append(
            "watchdog-deadline must be classified false — a deadline expiry is a bound, "
            "never an instrument-rewarded signal (plan §0.2)"
        )
    if "blocking_written" not in (baseline.get("excluded_event_kinds") or []):
        failures.append(
            "blocking_written must be pinned in excluded_event_kinds (plan §0.4)"
        )
    # Round-3 G4 extended by round-5 F-6 to BOTH registries: one owner per
    # fact — a registry's signal/bound class OWNS its kind's NO-relevance; the
    # baseline cites it. A contradiction is a fork of the fact, and a
    # registered kind with no class can never agree with any citation.
    for kind, clazz in registry_classes.items():
        cited = classified.get(kind)
        if isinstance(cited, bool) and (clazz == "signal") != cited:
            failures.append(
                f"no_relevant_kinds[{kind!r}]={cited} contradicts the registry "
                f"class {clazz!r} — the registry owns this fact; the baseline must cite it"
            )
    unclassed = registered - set(registry_classes)
    if unclassed:
        failures.append(
            f"kinds registered without a signal/bound class: {sorted(unclassed)} — "
            f"the registry owns NO-relevance and must declare it"
        )

    # --- baseline rows: cited or explicitly underived -------------------------------
    def _verify_citation(label: str, artifact: dict) -> None:
        raw_path = str(artifact.get("path", ""))
        artifact_path = Path(raw_path) if Path(raw_path).is_absolute() else repo / raw_path
        external = not str(artifact_path.resolve()).startswith(str(repo.resolve()) + os.sep)
        try:
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        except OSError as exc:
            if external:
                # Tri-state: an out-of-repo retained-run artifact may not exist on
                # this machine. "Could not check" is loud and distinct from
                # "passed" — the recorded digest remains the citation's authority.
                print(
                    f"check-acceptance: NOTE — {label}: external citation not "
                    f"verifiable here ({raw_path})"
                )
                return
            failures.append(f"{label}: cited artifact unreadable: {exc}")
            return
        if digest != artifact.get("sha256"):
            failures.append(f"{label}: cited artifact digest mismatch at {raw_path}")

    for index, row in enumerate(baseline.get("baseline_rows") or []):
        label = f"baseline_rows[{index}] ({row.get('metric')}/{row.get('run')})"
        artifact = row.get("artifact")
        artifact_list = row.get("artifacts")
        if artifact == "UNDERIVED":
            if not str(row.get("justification") or "").strip():
                failures.append(f"{label}: UNDERIVED without justification")
            continue
        if isinstance(artifact_list, list) and artifact_list:
            for item in artifact_list:
                if not isinstance(item, dict):
                    failures.append(f"{label}: artifacts entries must be path+sha256 objects")
                    continue
                _verify_citation(f"{label}/{item.get('role', '?')}", item)
            continue
        if not isinstance(artifact, dict):
            failures.append(
                f"{label}: artifact must be a path+sha256 object, an artifacts list, or UNDERIVED"
            )
            continue
        _verify_citation(label, artifact)

    # --- removal-ledger rows --------------------------------------------------------
    try:
        raw_rows = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, ValueError) as exc:
        _die(f"removal ledger unreadable: {ledger_path}: {exc}")

    for index, row in enumerate(raw_rows):
        label = f"removal_ledger[{index}]"
        forbidden = FORBIDDEN_LEDGER_FIELDS & set(row)
        if forbidden:
            failures.append(
                f"{label}: carries {sorted(forbidden)} — LOC derives solely from "
                f"git diff {pre_tag}..HEAD, never from a hand-maintained ledger field"
            )
        if row.get("kind") not in LEDGER_KINDS:
            failures.append(f"{label}: unknown kind {row.get('kind')!r}")
            continue
        if row.get("status") not in LEDGER_STATUSES:
            failures.append(f"{label}: unknown status {row.get('status')!r}")
            continue
        if row.get("status") != "landed":
            continue  # planned rows make no tree claim yet (scoped verification)
        subject = row.get("subject") or {}
        if row["kind"] == "delete":
            path = subject.get("path")
            if not path:
                failures.append(f"{label}: landed delete without a subject path")
            elif not _exists_at_tag(repo, pre_tag, path):
                failures.append(f"{label}: {path} did not exist at {pre_tag} — not a deletion")
            elif (repo / path).exists():
                failures.append(
                    f"{label}: {path} still exists at HEAD — the ledger lies about the tree"
                )
        elif row["kind"] == "add":
            path = subject.get("path")
            if not path:
                failures.append(f"{label}: landed add without a subject path")
            elif not (repo / path).exists():
                failures.append(f"{label}: {path} absent at HEAD — the ledger lies about the tree")
        elif row["kind"] == "gate-retire":
            gate = subject.get("gate")
            if not gate:
                failures.append(f"{label}: landed gate-retire without a subject gate")
                continue
            registry = (repo / "harness" / "gates.tsv").read_text(encoding="utf-8")
            live_ids = {
                line.split("\t", 1)[0]
                for line in registry.splitlines()
                if line.strip() and not line.startswith("#")
            }
            if gate in live_ids:
                failures.append(f"{label}: gate {gate} still registered in gates.tsv")
                continue
            # Round-3 D2: fail closed both directions — a retirement claim over a
            # gate that never existed at pre_tag is a fabrication, not a removal
            # (the delete branch already checks both directions; so must this).
            shown = _git(repo, "show", f"{pre_tag}:harness/gates.tsv")
            if shown.returncode != 0:
                _die(f"gates.tsv unreadable at {pre_tag}")
            retired_rows = [
                line for line in shown.stdout.splitlines() if line.startswith(f"{gate}\t")
            ]
            if not retired_rows:
                failures.append(
                    f"{label}: gate {gate} never existed at {pre_tag} — not a retirement"
                )
                continue
            # Dead verification code dies with its gate: the retired gate's pre_tag
            # probes must no longer collect anywhere in the suite.
            for line in retired_rows:
                for node_id in line.split("\t")[3].split(";"):
                    # Strip any parametrization suffix — the test FUNCTION is the
                    # survivable unit, not the node-id (round-3 D2).
                    test_name = node_id.split("::")[-1].split("[")[0].strip()
                    if not test_name:
                        continue
                    hits = _git(repo, "grep", "-l", f"def {test_name}", "--", "tests/")
                    if hits.returncode == 0 and hits.stdout.strip():
                        failures.append(
                            f"{label}: retired gate {gate}'s probe {test_name} survives "
                            f"in {hits.stdout.strip().splitlines()[0]}"
                        )
    return failures


def main() -> int:
    present = [name for name in AMBIENT_OVERRIDES if name in os.environ]
    if present:
        names = ", ".join(present)
        _die(f"ambient override present ({names}) — the environment is never authority")

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    arguments = parser.parse_args()
    repo = arguments.repo
    ledger = arguments.ledger or repo / "removal_ledger.jsonl"
    baseline = arguments.baseline or repo / "acceptance_baseline.json"

    failures = check(repo, ledger, baseline)
    if failures:
        for failure in failures:
            print(f"check-acceptance: RED — {failure}", file=sys.stderr)
        return 1
    print(
        "check-acceptance: GREEN — ledger tree claims, exhaustive kind "
        "classification, and baseline citations verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
