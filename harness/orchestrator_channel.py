#!/usr/bin/env python3
"""Durable activity and decision channel for the resident tmux Orchestrator.

The tmux pane is only a notification surface.  Complete bounded activity
snapshots live in an append-only, monotonically sequenced journal, and the
Orchestrator records its decision through this closed schema.  Its only
machine effect is monotone: ``block`` or ``no-op``.  It can never grant or
advance a Factory transition.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

from attention_gate import AttentionGateError, append_blocking_event  # noqa: E402
from jev_screen import ScreenError, validate_review  # noqa: E402
from lane_dialogue import (  # noqa: E402
    LaneDialogueError,
    pending_questions,
)
from run_guidance import (  # noqa: E402
    RunGuidanceError,
)
from run_guidance import (  # noqa: E402
    assessment_state as guidance_assessment_state,
)


class OrchestratorChannelError(RuntimeError):
    """The resident Orchestrator channel could not prove a safe result."""


_ACTIVITY_SCHEMA = "factory-orchestrator-activity/1"
_LEGACY_ASSESSMENT_SCHEMA = "factory-orchestrator-assessment/2"
_ASSESSMENT_SCHEMA = "factory-orchestrator-assessment/3"
_SCREEN_ASSESSMENT_SCHEMA = "factory-orchestrator-assessment/4"
# assessment/5 adds the code-and-progress review and the signal class (founder direction
# 2026-10-16: the Orchestrator assesses the code and progress, not merely reports status).
_PROGRESS_ASSESSMENT_SCHEMA = "factory-orchestrator-assessment/5"
_GUIDANCE_SCHEMAS = frozenset(
    {_ASSESSMENT_SCHEMA, _SCREEN_ASSESSMENT_SCHEMA, _PROGRESS_ASSESSMENT_SCHEMA}
)
_REPORT_SCHEMA = "factory-orchestrator-report/1"
_KIN_ID = re.compile(r"^[0-9a-f]{12}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
#: The stop decision the Orchestrator held before 0.8.20 (founder, 2026-10-18: the run never
#: stops; it blocks, fixes and continues). Accepted only when replaying a recorded report.
_RETIRED_STOP_DECISION = "halt"  # gate-exempt: replay of reports recorded before 0.8.20
_ACTIVITY_KINDS = frozenset(
    {
        "pane_delta",
        "cadence",
        "deterministic_signal",
        "pre_dispatch",
        "pre_verdict",
        "phase_transition",
        "user_imperative",
        "pre_commit",
        "pre_first_write",
    }
)
_ACTIVITY_SOURCES = frozenset({"dispatcher", "validator", "coder", "tester", "founder"})
# A parallel lane instance (tester-b) reports under its own identity, like lane_dialogue._LANE.
_LANE_INSTANCE_SOURCE = re.compile(r"^(?:coder|tester)-[a-z0-9][a-z0-9-]{0,15}$")


def _activity_source(source: object) -> bool:
    return isinstance(source, str) and (
        source in _ACTIVITY_SOURCES or _LANE_INSTANCE_SOURCE.fullmatch(source) is not None
    )
_LATEST_INPUT_CLASSES = frozenset({"override", "aside", "intensity-change", "refinement", "none"})
_PLANNING_MODES = frozenset({"direct", "clarify", "decompose", "deep"})
_COMPLEXITY_LEVELS = frozenset({"low", "medium", "high"})
_AMBIGUITY_LEVELS = frozenset({"low", "medium", "high"})
_REQUIREMENT_PROVENANCE = frozenset(
    {"explicit-user", "ratified-artifact", "implicit-assumption", "inherited-code"}
)
_COMPLEXITY_EFFECTS = frozenset({"high", "disproportionate"})
_COMPLEXITY_DRIVERS = frozenset({"intrinsic", "interaction", "assumption"})
_HOTSPOT_DISPOSITIONS = frozenset({"confirmed-required", "derived-constraint", "question-required"})
_JUDGING_PASS_STATES = frozenset({"not-started", "active", "complete"})
_HARNESS_STATES = frozenset({"open", "closed", "no"})
_GUIDANCE_STATES = frozenset(
    {"none", "pending-application", "routing-verified", "evidence-complete", "noncompliant"}
)
_FALSE_CLOSE = re.compile(
    r"\b(?:run\s+(?:is\s+|[A-Za-z0-9._/-]+\s+is\s+)?|officially\s+)"
    r"(?:closed|complete|completed|done|finished)\b",
    re.IGNORECASE,
)
# The human surface's classes, most urgent first. A class classifies the fact; escalation (page
# or ticket) is decided downstream, never here.
_SIGNAL_CLASSES = ("critical", "action-required", "at-your-convenience", "informative")
_SIGNAL_RANK = {name: rank for rank, name in enumerate(reversed(_SIGNAL_CLASSES))}
_PROGRESS_TRENDS = frozenset({"ahead", "on-track", "behind", "stalled"})
_REQUIREMENT_STATES = frozenset({"done", "in-progress", "not-started", "blocked"})
# A slice boundary always owes a code-and-progress review; so does every Nth cadence tick.
_PROGRESS_BOUNDARY_KINDS = frozenset({"pre_dispatch", "pre_verdict", "phase_transition"})
_PROGRESS_REVIEW_EVERY_TICKS = 2
# Evidence is the work itself: a commit, or a path (optionally :line or :start-end) into a lane
# tree, a promoted tree, or the Validator's verdict and test records.
_COMMIT_REF = re.compile(r"^(?:commit:)?[0-9a-f]{7,40}$")
_PATH_REF = re.compile(r"^[A-Za-z0-9_.@~+/-]*[/.][A-Za-z0-9_.@~+/-]*(?::\d+(?:-\d+)?)?$")
# What the lanes and the Orchestrator say about the work is status, not evidence of it.
_STATUS_REFS = re.compile(
    r"(?:^|/)(?:orchestrator/(?:activity|reports)\.jsonl|OUTSTANDING-WORK\.md"
    r"|dialogue/journal\.jsonl)(?::|$)|-lane\.log(?::|$)"
)
# ponytail: a raised ceiling (run e passed 32 MiB); rotate reports.jsonl or validate from the
# last accepted cursor when a run outgrows it.
_MAX_JOURNAL_BYTES = 128 * 1024 * 1024
_MAX_ACTIVITY_BYTES = 64 * 1024
_MAX_REPORT_BYTES = 64 * 1024


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _bounded_text(value: object, *, field: str, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorChannelError(f"{field} must be non-empty text")
    if len(value.encode("utf-8")) > maximum:
        raise OrchestratorChannelError(f"{field} exceeds its byte ceiling")
    return value


def _text_list(value: object, *, field: str, maximum_items: int = 32) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise OrchestratorChannelError(f"{field} must be a bounded list")
    return [_bounded_text(item, field=field, maximum=4096) for item in value]


def _work_refs(value: object, *, field: str) -> list[str]:
    refs = _text_list(value, field=field)
    if not refs:
        raise OrchestratorChannelError(f"{field} must cite the work: a commit or a path")
    for ref in refs:
        if not (_COMMIT_REF.fullmatch(ref) or _PATH_REF.fullmatch(ref)):
            raise OrchestratorChannelError(f"{field} entry is not a commit or a path: {ref[:80]}")
        if _STATUS_REFS.search(ref):
            raise OrchestratorChannelError(
                f"{field} cites status, not the work itself: {ref[:80]}"
            )
    return refs


def _signal_class(value: object, *, field: str) -> str:
    if value not in _SIGNAL_CLASSES:
        raise OrchestratorChannelError(f"{field} must be one of {', '.join(_SIGNAL_CLASSES)}")
    return str(value)


def _progress_review(value: object) -> dict[str, Any] | None:
    """Check the code-and-progress review: the work read and judged, with evidence."""

    if value is None:
        return None
    expected = {"evidence", "requirements_progress", "trend", "trend_basis", "code_findings"}
    if not isinstance(value, dict) or set(value) != expected:
        raise OrchestratorChannelError("progress_review has unknown or missing fields")
    _work_refs(value.get("evidence"), field="progress_review evidence")
    progress = value.get("requirements_progress")
    if not isinstance(progress, list) or not progress or len(progress) > 32:
        raise OrchestratorChannelError(
            "progress_review must judge progress on at least one requirement"
        )
    for item in progress:
        if not isinstance(item, dict) or set(item) != {"requirement", "state", "evidence"}:
            raise OrchestratorChannelError(
                "requirements_progress entry has unknown or missing fields"
            )
        _bounded_text(item.get("requirement"), field="requirements_progress requirement")
        if item.get("state") not in _REQUIREMENT_STATES:
            raise OrchestratorChannelError("requirements_progress has an invalid state")
        if item["state"] != "not-started":
            _work_refs(item.get("evidence"), field="requirements_progress evidence")
        else:
            _text_list(item.get("evidence"), field="requirements_progress evidence")
    if value.get("trend") not in _PROGRESS_TRENDS:
        raise OrchestratorChannelError("progress_review has an invalid trend")
    _bounded_text(value.get("trend_basis"), field="progress_review trend_basis")
    findings = value.get("code_findings")
    if not isinstance(findings, list) or len(findings) > 32:
        raise OrchestratorChannelError("progress_review code_findings must be a bounded list")
    for item in findings:
        if not isinstance(item, dict) or set(item) != {"finding", "refs", "signal_class"}:
            raise OrchestratorChannelError("code_findings entry has unknown or missing fields")
        _bounded_text(item.get("finding"), field="code_findings finding")
        _work_refs(item.get("refs"), field="code_findings refs")
        _signal_class(item.get("signal_class"), field="code_findings signal_class")
    return dict(value)


def progress_review_every(harness: Mapping[str, Any]) -> int | None:
    """Cadence ticks between required code-and-progress reviews, or None for a legacy run."""

    config = harness.get("progress_review")
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) != {"every_cadence_ticks"}:
        raise OrchestratorChannelError("harness progress_review configuration is malformed")
    every = config["every_cadence_ticks"]
    if not isinstance(every, int) or isinstance(every, bool) or not 1 <= every <= 8:
        raise OrchestratorChannelError("progress_review every_cadence_ticks must be 1-8")
    return every


def progress_review_due(
    activity_rows: Sequence[Mapping[str, object]],
    report_rows: Sequence[Mapping[str, object]],
    *,
    through: int,
    every: int = _PROGRESS_REVIEW_EVERY_TICKS,
) -> bool:
    """Whether an assessment through ``through`` owes a code-and-progress review.

    It is owed when the unassessed range crosses a slice boundary (pre-dispatch, pre-verdict,
    phase transition), or when ``every`` cadence ticks have passed since the last review.
    """

    previous = 0
    reviewed = 0
    for row in report_rows:
        assessment = row.get("assessment")
        if not isinstance(assessment, dict):
            continue
        cursor = int(assessment.get("through_cursor") or 0)
        if cursor >= through:
            continue
        previous = max(previous, cursor)
        if assessment.get("progress_review") is not None:
            reviewed = max(reviewed, cursor)
    ticks = 0
    for row in activity_rows:
        position = row.get("cursor")
        if not isinstance(position, int) or position > through:
            continue
        if position > previous and row.get("kind") in _PROGRESS_BOUNDARY_KINDS:
            return True
        if position > reviewed and row.get("kind") == "cadence":
            ticks += 1
    return ticks >= every


def progress_review_due_now(root: pathlib.Path, through: int) -> bool | None:
    """For the dispatcher's notice: None on a legacy run, else whether ``through`` owes one."""

    root = pathlib.Path(root)
    every = progress_review_every(_read_harness(root))
    if every is None:
        return None
    directory = root / "orchestrator"
    return progress_review_due(
        _read_jsonl(directory / "activity.jsonl"), _read_jsonl(directory / "reports.jsonl"),
        through=through, every=every,
    )


def _complexity_hotspots(value: object) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value, list) or len(value) > 32:
        raise OrchestratorChannelError("complexity_hotspots must be a bounded list")
    expected = {
        "requirement",
        "provenance",
        "complexity_effect",
        "complexity_basis",
        "driver",
        "interacts_with",
        "assumptions",
        "simpler_path",
        "disposition",
        "basis",
        "clarifying_question",
        "kindex_node_id",
    }
    hotspots: list[dict[str, Any]] = []
    questions: list[str] = []
    for number, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != expected:
            raise OrchestratorChannelError(
                f"complexity hotspot {number} has unknown or missing fields"
            )
        _bounded_text(item["requirement"], field="complexity hotspot requirement")
        _bounded_text(item["complexity_basis"], field="complexity hotspot basis")
        _bounded_text(item["simpler_path"], field="complexity hotspot simpler_path")
        if item["provenance"] not in _REQUIREMENT_PROVENANCE:
            raise OrchestratorChannelError("complexity hotspot has invalid provenance")
        if item["complexity_effect"] not in _COMPLEXITY_EFFECTS:
            raise OrchestratorChannelError("complexity hotspot has invalid effect")
        if item["driver"] not in _COMPLEXITY_DRIVERS:
            raise OrchestratorChannelError("complexity hotspot has invalid driver")
        interactions = _text_list(item["interacts_with"], field="complexity hotspot interactions")
        assumptions = _text_list(item["assumptions"], field="complexity hotspot assumptions")
        if item["driver"] == "interaction" and not interactions:
            raise OrchestratorChannelError(
                "an interaction complexity hotspot must name the interacting requirement"
            )
        if (
            item["driver"] == "assumption"
            or item["provenance"] in {"implicit-assumption", "inherited-code"}
        ) and not assumptions:
            raise OrchestratorChannelError(
                "an assumed or inherited complexity hotspot must expose its assumptions"
            )
        disposition = item["disposition"]
        if disposition not in _HOTSPOT_DISPOSITIONS:
            raise OrchestratorChannelError("complexity hotspot has invalid disposition")
        basis = item["basis"]
        question = item["clarifying_question"]
        if disposition == "question-required":
            if basis is not None:
                raise OrchestratorChannelError(
                    "an unresolved complexity hotspot cannot claim a closure basis"
                )
            questions.append(
                _bounded_text(question, field="complexity hotspot clarifying_question")
            )
        else:
            _bounded_text(basis, field="complexity hotspot closure basis")
            if question is not None:
                raise OrchestratorChannelError(
                    "a closed complexity hotspot cannot retain a clarifying question"
                )
        node_id = item["kindex_node_id"]
        if node_id is not None and (not isinstance(node_id, str) or not _KIN_ID.fullmatch(node_id)):
            raise OrchestratorChannelError("complexity hotspot has an invalid Kindex node id")
        hotspots.append(dict(item))
    return hotspots, questions


def _open_directory(path: pathlib.Path) -> int:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OrchestratorChannelError(f"not a real directory: {path}")
    return descriptor


def _channel_directory(root: pathlib.Path) -> pathlib.Path:
    if root.is_symlink() or not root.is_dir():
        raise OrchestratorChannelError(f"run root is not a real directory: {root}")
    directory = root / "orchestrator"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    descriptor = _open_directory(directory)
    try:
        os.fchmod(descriptor, 0o700)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return directory


@contextmanager
def _channel_lock(root: pathlib.Path) -> Iterator[pathlib.Path]:
    directory = _channel_directory(root)
    lock_path = directory / ".channel.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OrchestratorChannelError("channel lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield directory
    finally:
        os.close(descriptor)


def _read_jsonl(path: pathlib.Path, *, maximum: int = _MAX_JOURNAL_BYTES) -> list[dict[str, Any]]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        return []
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OrchestratorChannelError(f"journal is not regular: {path}")
        if metadata.st_size > maximum:
            raise OrchestratorChannelError(f"journal exceeds its byte ceiling: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            rows: list[dict[str, Any]] = []
            for number, line in enumerate(stream, 1):
                if not line.endswith("\n"):
                    raise OrchestratorChannelError(
                        f"journal has an incomplete row {number}: {path}"
                    )
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise OrchestratorChannelError(
                        f"journal row {number} is not JSON: {path}"
                    ) from exc
                if not isinstance(row, dict):
                    raise OrchestratorChannelError(f"journal row {number} is not an object: {path}")
                rows.append(row)
            installed = os.lstat(path)
            if stat.S_ISLNK(installed.st_mode) or (
                installed.st_dev,
                installed.st_ino,
            ) != (metadata.st_dev, metadata.st_ino):
                raise OrchestratorChannelError(f"journal changed while read: {path}")
            return rows
    except UnicodeDecodeError as exc:
        raise OrchestratorChannelError(f"journal is not UTF-8: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _append(path: pathlib.Path, row: Mapping[str, object]) -> None:
    payload = _canonical(dict(row)) + b"\n"
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_APPEND
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OrchestratorChannelError(f"journal is not regular: {path}")
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count < 1:
                raise OrchestratorChannelError("journal append made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = _open_directory(path.parent)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _validate_activity_rows(rows: Sequence[Mapping[str, object]]) -> int:
    expected_cursor = 1
    expected = {
        "schema_version",
        "cursor",
        "ts",
        "kind",
        "source",
        "detail",
        "snapshot",
        "snapshot_sha256",
    }
    for row in rows:
        if set(row) != expected or row.get("schema_version") != _ACTIVITY_SCHEMA:
            raise OrchestratorChannelError("activity journal has a malformed row")
        if row.get("cursor") != expected_cursor:
            raise OrchestratorChannelError("activity journal cursor is not contiguous")
        if row.get("kind") not in _ACTIVITY_KINDS or not _activity_source(row.get("source")):
            raise OrchestratorChannelError("activity journal has an invalid kind or source")
        detail = _bounded_text(row.get("detail"), field="activity detail", maximum=8192)
        snapshot = row.get("snapshot")
        if not isinstance(snapshot, str) or len(snapshot.encode("utf-8")) > _MAX_ACTIVITY_BYTES:
            raise OrchestratorChannelError("activity snapshot exceeds its byte ceiling")
        digest = "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        if row.get("snapshot_sha256") != digest or not detail:
            raise OrchestratorChannelError("activity snapshot digest is invalid")
        expected_cursor += 1
    return expected_cursor - 1


def append_activity(
    root: pathlib.Path,
    *,
    kind: str,
    source: str,
    detail: str,
    snapshot: str = "",
) -> int:
    """Append every observed delta without semantic selection and return its cursor."""

    if kind not in _ACTIVITY_KINDS:
        raise OrchestratorChannelError(f"invalid activity kind: {kind}")
    if not _activity_source(source):
        raise OrchestratorChannelError(f"invalid activity source: {source}")
    detail = _bounded_text(detail, field="activity detail", maximum=8192)
    if not isinstance(snapshot, str) or len(snapshot.encode("utf-8")) > _MAX_ACTIVITY_BYTES:
        raise OrchestratorChannelError("activity snapshot exceeds its byte ceiling")
    with _channel_lock(pathlib.Path(root)) as directory:
        path = directory / "activity.jsonl"
        rows = _read_jsonl(path)
        cursor = _validate_activity_rows(rows) + 1
        _append(
            path,
            {
                "schema_version": _ACTIVITY_SCHEMA,
                "cursor": cursor,
                "ts": _now(),
                "kind": kind,
                "source": source,
                "detail": detail,
                "snapshot": snapshot,
                "snapshot_sha256": "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
            },
        )
        return cursor


def activity_highwater(root: pathlib.Path) -> int:
    with _channel_lock(pathlib.Path(root)) as directory:
        return _validate_activity_rows(_read_jsonl(directory / "activity.jsonl"))


def assessed_through(root: pathlib.Path) -> int:
    """Highest cursor the Orchestrator has recorded an assessment through.

    A cheap progress read for the dispatcher's liveness watch; full report
    validation stays with require_current/require_through.
    """

    with _channel_lock(pathlib.Path(root)) as directory:
        rows = _read_jsonl(directory / "reports.jsonl")
    cursors = [
        row["assessment"].get("through_cursor")
        for row in rows
        if isinstance(row.get("assessment"), dict)
    ]
    return max((c for c in cursors if isinstance(c, int) and not isinstance(c, bool)), default=0)


def _read_harness(root: pathlib.Path) -> dict[str, Any]:
    path = root / "harness.json"
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise OrchestratorChannelError(f"harness state cannot be read: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_REPORT_BYTES:
            raise OrchestratorChannelError("harness state is not a bounded regular file")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            document = json.load(stream)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrchestratorChannelError("harness state is not valid UTF-8 JSON") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(document, dict) or document.get("status") not in _HARNESS_STATES:
        raise OrchestratorChannelError("harness status is missing or invalid")
    return document


def _guidance_expected(root: pathlib.Path, harness: Mapping[str, Any]) -> dict[str, Any] | None:
    if "guidance_contract_version" not in harness:
        return None
    try:
        return guidance_assessment_state(root, root / "artifacts")
    except (OSError, RunGuidanceError) as exc:
        raise OrchestratorChannelError(f"run guidance state cannot be derived: {exc}") from exc


def _nullable_digest(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise OrchestratorChannelError(f"assessment {field} must be null or a sha256 digest")
    return value


def _guidance_fields(value: Mapping[str, Any]) -> dict[str, Any]:
    state = value.get("guidance_state")
    if state not in _GUIDANCE_STATES:
        raise OrchestratorChannelError("assessment has an invalid guidance_state")
    selection = _nullable_digest(
        value.get("guidance_selection_digest"), field="guidance_selection_digest"
    )
    application = _nullable_digest(
        value.get("guidance_application_digest"), field="guidance_application_digest"
    )
    evidence = _nullable_digest(
        value.get("guidance_evidence_digest"), field="guidance_evidence_digest"
    )
    findings = _text_list(value.get("guidance_findings"), field="guidance_findings")
    if findings != sorted(set(findings)):
        raise OrchestratorChannelError("assessment guidance_findings must be sorted and unique")
    if state == "none" and any(item is not None for item in (selection, application, evidence)):
        raise OrchestratorChannelError("none guidance state cannot carry digests")
    if state == "pending-application" and (
        selection is None or application is not None or evidence is not None or findings
    ):
        raise OrchestratorChannelError("pending guidance state has inconsistent evidence")
    if state == "routing-verified" and (
        selection is None or application is None or evidence is not None or findings
    ):
        raise OrchestratorChannelError("routing-verified guidance state has inconsistent evidence")
    if state == "evidence-complete" and (
        selection is None or application is None or evidence is None or findings
    ):
        raise OrchestratorChannelError("evidence-complete guidance state has inconsistent evidence")
    if state == "noncompliant" and not findings:
        raise OrchestratorChannelError("noncompliant guidance state must identify a finding")
    if state != "noncompliant" and findings:
        raise OrchestratorChannelError("only noncompliant guidance state may carry findings")
    return {
        "state": state,
        "selection_digest": selection,
        "application_digest": application,
        "evidence_digest": evidence,
        "findings": findings,
    }


def _validate_assessment(
    value: object,
    *,
    highwater: int,
    current_harness_state: str | None = None,
    pending_lane_questions: Sequence[str] = (),
    guidance_expected: Mapping[str, Any] | None = None,
    recorded: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OrchestratorChannelError("assessment must be an object")
    base_fields = {
        "schema_version",
        "through_cursor",
        "ultimate_goal",
        "current_action",
        "latest_input",
        "latest_input_class",
        "classified_because",
        "direction_correct",
        "if_continued",
        "side_effects",
        "desirable_outcome",
        "advances_goal",
        "aligned",
        "adherence_findings",
        "task_complexity",
        "latent_ambiguity",
        "requirements_considered",
        "complexity_hotspots",
        "planning_mode",
        "specification_questions",
        "work_breakdown",
        "model_routing",
        "causal_hypotheses",
        "outcome_discriminators",
        "dispatch_context_mode",
        "kindex_state_updates",
        "recommended_strategy",
        "judging_pass_state",
        "observed_harness_status",
        "run_state_basis",
        "outstanding_work",
        "decision",
        "summary",
        "kindex_status",
        "kindex_context",
        "kindex_basis",
    }
    guidance_fields = {
        "guidance_state",
        "guidance_selection_digest",
        "guidance_application_digest",
        "guidance_evidence_digest",
        "guidance_findings",
    }
    schema = value.get("schema_version")
    if schema == _LEGACY_ASSESSMENT_SCHEMA:
        expected = base_fields
    elif schema in _GUIDANCE_SCHEMAS:
        expected = base_fields | guidance_fields
        if schema == _SCREEN_ASSESSMENT_SCHEMA:
            expected = expected | {"screen_review"}
        if schema == _PROGRESS_ASSESSMENT_SCHEMA:
            expected = expected | {"screen_review", "signal_class", "progress_review"}
    else:
        expected = set()
    if set(value) != expected:
        raise OrchestratorChannelError("assessment has unknown or missing fields")
    checked_guidance: dict[str, Any] | None = None
    if schema in _GUIDANCE_SCHEMAS:
        checked_guidance = _guidance_fields(value)
    if guidance_expected is not None:
        if checked_guidance is None:
            raise OrchestratorChannelError("current run requires assessment/3 guidance evidence")
        if checked_guidance != dict(guidance_expected):
            raise OrchestratorChannelError(
                "assessment guidance state differs from the retained run record"
            )
    cursor = value.get("through_cursor")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 1 or cursor > highwater:
        raise OrchestratorChannelError("assessment through_cursor is outside the activity journal")
    for field in (
        "ultimate_goal",
        "current_action",
        "latest_input",
        "classified_because",
        "if_continued",
        "recommended_strategy",
        "run_state_basis",
        "summary",
        "kindex_basis",
    ):
        _bounded_text(value.get(field), field=field)
    if value.get("latest_input_class") not in _LATEST_INPUT_CLASSES:
        raise OrchestratorChannelError("assessment has an invalid latest_input_class")
    for field in ("direction_correct", "desirable_outcome", "advances_goal", "aligned"):
        if not isinstance(value.get(field), bool):
            raise OrchestratorChannelError(f"assessment {field} must be boolean")
    _text_list(value.get("side_effects"), field="side_effects")
    findings = _text_list(value.get("adherence_findings"), field="adherence_findings")
    requirements = _text_list(value.get("requirements_considered"), field="requirements_considered")
    if not requirements:
        raise OrchestratorChannelError(
            "assessment must name the requirements considered before decomposition"
        )
    hotspots, simplification_questions = _complexity_hotspots(value.get("complexity_hotspots"))
    questions = _text_list(value.get("specification_questions"), field="specification_questions")
    work_breakdown = _text_list(value.get("work_breakdown"), field="work_breakdown")
    model_routing = _text_list(value.get("model_routing"), field="model_routing")
    hypotheses = _text_list(value.get("causal_hypotheses"), field="causal_hypotheses")
    discriminators = _text_list(value.get("outcome_discriminators"), field="outcome_discriminators")
    _text_list(value.get("outstanding_work"), field="outstanding_work")
    complexity = value.get("task_complexity")
    ambiguity = value.get("latent_ambiguity")
    planning_mode = value.get("planning_mode")
    if complexity not in _COMPLEXITY_LEVELS:
        raise OrchestratorChannelError("assessment has an invalid task_complexity")
    if complexity == "high" and not hotspots:
        raise OrchestratorChannelError(
            "high task complexity requires its requirement pressure points to be exposed"
        )
    if ambiguity not in _AMBIGUITY_LEVELS:
        raise OrchestratorChannelError("assessment has an invalid latent_ambiguity")
    if planning_mode not in _PLANNING_MODES:
        raise OrchestratorChannelError("assessment has an invalid planning_mode")
    if ambiguity == "high" and planning_mode not in {"clarify", "deep"}:
        raise OrchestratorChannelError(
            "high latent ambiguity requires clarify or deep planning mode"
        )
    if planning_mode == "clarify" and not questions:
        raise OrchestratorChannelError("clarify planning mode requires a specification question")
    missing_simplification_questions = sorted(set(simplification_questions) - set(questions))
    if missing_simplification_questions:
        raise OrchestratorChannelError(
            "assessment omits a required complexity-reducing clarification question"
        )
    if simplification_questions and planning_mode != "clarify":
        raise OrchestratorChannelError(
            "an unresolved requirement pressure point requires clarify planning mode"
        )
    missing_questions = sorted(set(pending_lane_questions) - set(questions))
    if missing_questions:
        raise OrchestratorChannelError("assessment omits a pending lane specification question")
    if pending_lane_questions and planning_mode != "clarify":
        raise OrchestratorChannelError("a pending lane question requires clarify planning mode")
    if planning_mode in {"decompose", "deep"} and not work_breakdown:
        raise OrchestratorChannelError(f"{planning_mode} planning mode requires a work breakdown")
    if work_breakdown and not model_routing:
        raise OrchestratorChannelError("a work breakdown requires explicit model routing")
    if value.get("dispatch_context_mode") != "chunk-specific":
        raise OrchestratorChannelError(
            "dispatch context must be a chunk-specific projection, not a context dump"
        )
    if hypotheses and not discriminators:
        raise OrchestratorChannelError(
            "causal hypotheses require pre-registered outcome discriminators"
        )
    if value.get("judging_pass_state") not in _JUDGING_PASS_STATES:
        raise OrchestratorChannelError("assessment has an invalid judging_pass_state")
    observed_harness_status = value.get("observed_harness_status")
    if observed_harness_status not in _HARNESS_STATES:
        raise OrchestratorChannelError("assessment has an invalid observed_harness_status")
    if current_harness_state is not None and observed_harness_status != current_harness_state:
        raise OrchestratorChannelError(
            "assessment lifecycle claim does not match authoritative harness status"
        )
    if observed_harness_status == "open":
        lifecycle_text = "\n".join(
            str(value[field])
            for field in (
                "current_action",
                "if_continued",
                "recommended_strategy",
                "run_state_basis",
                "summary",
            )
        )
        if _FALSE_CLOSE.search(lifecycle_text):
            raise OrchestratorChannelError(
                "an open harness cannot be described as a closed or complete run"
            )
    decision = value.get("decision")
    # A report already in the journal before 0.8.20 may carry the retired stop decision;
    # it replays as the block it also wrote. A new assessment is block or no-op only.
    allowed = {"block", "no-op", *((_RETIRED_STOP_DECISION,) if recorded else ())}
    if decision not in allowed:
        raise OrchestratorChannelError("assessment decision must be block or no-op")
    should_block = (
        not value["direction_correct"]
        or not value["desirable_outcome"]
        or not value["advances_goal"]
        or not value["aligned"]
        or bool(findings)
        or bool(simplification_questions)
        or planning_mode == "clarify"
        or bool(checked_guidance and checked_guidance["state"] == "noncompliant")
        or bool(checked_guidance and checked_guidance["findings"])
    )
    if should_block and decision == "no-op":
        raise OrchestratorChannelError("a divergent or non-adherent assessment must block")
    status = value.get("kindex_status")
    context = value.get("kindex_context")
    if status not in {"consulted", "unavailable"} or not isinstance(context, list):
        raise OrchestratorChannelError("assessment has invalid Kindex evidence")
    if len(context) > 32 or any(
        not isinstance(item, str) or not _KIN_ID.fullmatch(item) for item in context
    ):
        raise OrchestratorChannelError("assessment has invalid Kindex node ids")
    if status == "consulted" and not context:
        raise OrchestratorChannelError("a consulted Kindex assessment must cite context")
    if status == "unavailable" and context:
        raise OrchestratorChannelError("an unavailable Kindex assessment cannot cite context")
    updates = value.get("kindex_state_updates")
    if (
        not isinstance(updates, list)
        or len(updates) > 32
        or any(not isinstance(item, str) or not _KIN_ID.fullmatch(item) for item in updates)
    ):
        raise OrchestratorChannelError("assessment has invalid Kindex state-update ids")
    if planning_mode in {"decompose", "deep"} and status == "consulted" and not updates:
        raise OrchestratorChannelError(
            "decomposed work must be written to Kindex, not held as a context dump"
        )
    if status == "unavailable" and updates:
        raise OrchestratorChannelError(
            "an unavailable Kindex assessment cannot claim state updates"
        )
    hotspot_nodes = [item["kindex_node_id"] for item in hotspots]
    if status == "consulted" and hotspots:
        has_missing_node = any(node_id is None for node_id in hotspot_nodes)
        has_unreported_node = not set(hotspot_nodes) <= set(updates)
        if has_missing_node or has_unreported_node:
            raise OrchestratorChannelError(
                "requirement pressure points must be written as Kindex state updates"
            )
    if status == "unavailable" and any(node_id is not None for node_id in hotspot_nodes):
        raise OrchestratorChannelError("an unavailable Kindex assessment cannot cite hotspot state")
    if schema == _PROGRESS_ASSESSMENT_SCHEMA:
        _check_signal_class(value, decision=str(decision), questions=questions)
    if len(_canonical(value)) > _MAX_REPORT_BYTES:
        raise OrchestratorChannelError("assessment exceeds its byte ceiling")
    return dict(value)


def _check_signal_class(value: Mapping[str, Any], *, decision: str, questions: list[str]) -> None:
    """The class must be at least as urgent as what the assessment itself says."""

    signal = _signal_class(value.get("signal_class"), field="assessment signal_class")
    review = _progress_review(value.get("progress_review"))
    floor = "informative"
    if questions:
        floor = "action-required"
    elif decision == "block" or (review is not None and review["trend"] == "stalled"):
        floor = "at-your-convenience"
    for finding in review["code_findings"] if review is not None else []:
        if _SIGNAL_RANK[finding["signal_class"]] > _SIGNAL_RANK[floor]:
            floor = finding["signal_class"]
    if _SIGNAL_RANK[signal] < _SIGNAL_RANK[floor]:
        raise OrchestratorChannelError(
            f"assessment signal_class {signal} is below what it reports ({floor})"
        )


def _validate_report_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    highwater: int,
) -> int:
    latest = 0
    seen_cursors: set[int] = set()
    expected = {"schema_version", "recorded_at", "assessment_digest", "assessment"}
    for row in rows:
        if set(row) != expected or row.get("schema_version") != _REPORT_SCHEMA:
            raise OrchestratorChannelError("report journal has a malformed row")
        assessment = _validate_assessment(
            row.get("assessment"), highwater=highwater, recorded=True
        )
        digest = "sha256:" + hashlib.sha256(_canonical(assessment)).hexdigest()
        if row.get("assessment_digest") != digest:
            raise OrchestratorChannelError("report assessment digest is invalid")
        cursor = assessment.get("through_cursor")
        if (
            not isinstance(cursor, int)
            or isinstance(cursor, bool)
            or cursor <= latest
            or cursor in seen_cursors
        ):
            raise OrchestratorChannelError("report cursors regress")
        seen_cursors.add(cursor)
        latest = cursor
    return latest


def record_assessment(root: pathlib.Path, value: object) -> dict[str, Any]:
    """Record one checked assessment and apply its monotone block effect."""

    root = pathlib.Path(root)
    with _channel_lock(root) as directory:
        activity_rows = _read_jsonl(directory / "activity.jsonl")
        highwater = _validate_activity_rows(activity_rows)
        dialogue = root / "dialogue" / "journal.jsonl"
        try:
            pending = (
                [str(row["text"]) for row in pending_questions(root)]
                if dialogue.exists() or dialogue.is_symlink()
                else []
            )
        except LaneDialogueError as exc:
            raise OrchestratorChannelError(f"lane dialogue is invalid: {exc}") from exc
        harness = _read_harness(root)
        assessment = _validate_assessment(
            value,
            highwater=highwater,
            current_harness_state=str(harness["status"]),
            pending_lane_questions=pending,
            guidance_expected=_guidance_expected(root, harness),
        )
        report_path = directory / "reports.jsonl"
        existing = _read_jsonl(report_path)
        previous_cursor = _validate_report_rows(existing, highwater=highwater)
        cursor = int(assessment["through_cursor"])
        _require_progress_review(harness, activity_rows, existing, assessment)
        try:
            review_previous = previous_cursor
            if cursor == previous_cursor and existing:
                review_previous = (existing[-2]["assessment"]["through_cursor"]
                                   if len(existing) > 1 else 0)
            validate_review(directory, activity_rows, harness.get("jev_screen"),
                            review_previous, cursor, assessment.get("screen_review"),
                            assessment["decision"], assessment["adherence_findings"])
        except ScreenError as exc:
            raise OrchestratorChannelError(str(exc)) from exc
        if cursor < previous_cursor:
            raise OrchestratorChannelError("assessment is older than the retained report cursor")
        digest = "sha256:" + hashlib.sha256(_canonical(assessment)).hexdigest()
        matching = [row for row in existing if row.get("assessment_digest") == digest]
        if matching:
            report = dict(matching[0])
        else:
            if cursor == previous_cursor and existing:
                raise OrchestratorChannelError("assessment conflicts at an existing cursor")
            report = {
                "schema_version": _REPORT_SCHEMA,
                "recorded_at": _now(),
                "assessment_digest": digest,
                "assessment": assessment,
            }
            if assessment["decision"] == "block":
                # Publish the monotone effect before the report that can make the
                # cursor current. A crash may leave a conservative orphaned block,
                # but can never leave a current BLOCK report with no gate effect.
                trigger = next(row for row in activity_rows if row.get("cursor") == cursor)
                try:
                    append_blocking_event(
                        root,
                        "validator",
                        {
                            "ts": trigger["ts"],
                            "class": "orchestrator_response",
                            "response": assessment["summary"],
                            "wake": f"activity-cursor:{assessment['through_cursor']}",
                            "trust_class": "untrusted-advisory",
                            "effect_route": "validator-blocking-only",
                        },
                    )
                except AttentionGateError as exc:
                    raise OrchestratorChannelError(
                        f"orchestrator block effect could not become durable: {exc}"
                    ) from exc
            _append(report_path, report)
    return report


def _require_progress_review(
    harness: Mapping[str, Any],
    activity_rows: Sequence[Mapping[str, object]],
    report_rows: Sequence[Mapping[str, object]],
    assessment: Mapping[str, Any],
) -> None:
    every = progress_review_every(harness)
    schema = assessment["schema_version"]
    if every is None and schema != _PROGRESS_ASSESSMENT_SCHEMA:
        return
    if schema != _PROGRESS_ASSESSMENT_SCHEMA:
        raise OrchestratorChannelError(
            "this run requires assessment/5: a code-and-progress review and a signal class"
        )
    cursor = int(assessment["through_cursor"])
    due = progress_review_due(activity_rows, report_rows, through=cursor,
                              every=every or _PROGRESS_REVIEW_EVERY_TICKS)
    if due and assessment.get("progress_review") is None:
        raise OrchestratorChannelError(
            "a code-and-progress review is due at this cursor: read the work and cite it; "
            "a status recap does not satisfy it"
        )


def resident_mode(root: pathlib.Path) -> bool:
    try:
        metadata = json.loads((pathlib.Path(root) / "harness.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(metadata, dict) and metadata.get("orchestrator_mode") == "resident-monitoring"


def require_resident(root: pathlib.Path) -> None:
    """Refuse a run whose metadata does not declare a resident Orchestrator."""

    if not resident_mode(root):
        raise OrchestratorChannelError(
            "run has no resident Orchestrator: every Factory run requires one "
            "(orchestrator_mode=resident-monitoring); there is no orchestrator-less mode"
        )


def _latest_assessment(rows: Sequence[Mapping[str, object]]) -> Mapping[str, Any] | None:
    if not rows:
        return None
    value = rows[-1].get("assessment")
    return value if isinstance(value, dict) else None


def _require_current_guidance(
    root: pathlib.Path,
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Any] | None:
    harness = _read_harness(root)
    expected = _guidance_expected(root, harness)
    latest = _latest_assessment(rows)
    if latest is not None:
        previous_assessment = _latest_assessment(rows[:-1])
        previous = int(previous_assessment["through_cursor"]) if previous_assessment else 0
        directory = root / "orchestrator"
        activities = _read_jsonl(directory / "activity.jsonl")
        try:
            validate_review(directory, activities, harness.get("jev_screen"), previous,
                            latest["through_cursor"], latest.get("screen_review"),
                            latest["decision"], latest["adherence_findings"])
        except ScreenError as exc:
            raise OrchestratorChannelError(str(exc)) from exc
    if expected is None:
        return None
    latest = _latest_assessment(rows)
    if latest is None or latest.get("schema_version") not in _GUIDANCE_SCHEMAS:
        raise OrchestratorChannelError("current run has no assessment/3 guidance evidence")
    if _guidance_fields(latest) != expected:
        raise OrchestratorChannelError(
            "resident Orchestrator guidance assessment is stale against the run record"
        )
    return expected


def require_current(root: pathlib.Path) -> tuple[int, int]:
    """Refuse any transition whose complete activity stream is unassessed.

    Every run has a resident Orchestrator (founder ruling 2026-09-21: exactly
    four roles, and the Orchestrator is always running). A run without one is
    refused here, never waved through: there is no orchestrator-less mode.
    """

    root = pathlib.Path(root)
    require_resident(root)
    with _channel_lock(root) as directory:
        highwater = _validate_activity_rows(_read_jsonl(directory / "activity.jsonl"))
        report_rows = _read_jsonl(directory / "reports.jsonl")
        reported = _validate_report_rows(report_rows, highwater=highwater)
        _require_current_guidance(root, report_rows)
    if highwater < 1 or reported != highwater:
        raise OrchestratorChannelError(
            f"resident Orchestrator is not current: activity={highwater} assessed={reported}"
        )
    return highwater, reported


def require_through(root: pathlib.Path, cursor: int) -> int:
    """Require an assessment covering one explicit transition checkpoint."""

    if cursor < 1:
        raise OrchestratorChannelError("required cursor must be positive")
    root = pathlib.Path(root)
    require_resident(root)
    with _channel_lock(root) as directory:
        activity_rows = _read_jsonl(directory / "activity.jsonl")
        highwater = _validate_activity_rows(activity_rows)
        report_rows = _read_jsonl(directory / "reports.jsonl")
        reported = _validate_report_rows(report_rows, highwater=highwater)
        guidance = _require_current_guidance(root, report_rows)
    if cursor > highwater:
        raise OrchestratorChannelError("required cursor is beyond the activity journal")
    if reported < cursor:
        raise OrchestratorChannelError(
            f"resident Orchestrator has not assessed checkpoint {cursor}: assessed={reported}"
        )
    checkpoint = next(row for row in activity_rows if row.get("cursor") == cursor)
    kind = checkpoint.get("kind")
    if guidance is not None and kind == "pre_dispatch" and guidance["state"] not in {
        "none",
        "routing-verified",
        "evidence-complete",
    }:
        raise OrchestratorChannelError(
            f"selected run guidance is not routing-verified before dispatch: {guidance['state']}"
        )
    if guidance is not None and kind == "pre_verdict" and guidance["state"] not in {
        "none",
        "evidence-complete",
    }:
        raise OrchestratorChannelError(
            f"selected run guidance is not evidence-complete before verdict: {guidance['state']}"
        )
    return reported


def _read_input(path: pathlib.Path) -> object:
    if path.is_symlink():
        raise OrchestratorChannelError("assessment input may not be a symlink")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OrchestratorChannelError(f"assessment input cannot be read: {exc}") from exc
    if not raw or len(raw) > _MAX_REPORT_BYTES:
        raise OrchestratorChannelError("assessment input is empty or oversized")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrchestratorChannelError("assessment input is not JSON") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    append = subparsers.add_parser("append")
    append.add_argument("--root", type=pathlib.Path, required=True)
    append.add_argument("--kind", choices=sorted(_ACTIVITY_KINDS), required=True)
    append.add_argument(
        "--source", required=True, help="dispatcher, validator, founder, or a lane id"
    )
    append.add_argument("--detail", required=True)
    append.add_argument("--snapshot-file", type=pathlib.Path)
    report = subparsers.add_parser("report")
    report.add_argument("--root", type=pathlib.Path, required=True)
    report.add_argument("--input", type=pathlib.Path, required=True)
    current = subparsers.add_parser("require-current")
    current.add_argument("--root", type=pathlib.Path, required=True)
    through = subparsers.add_parser("require-through")
    through.add_argument("--root", type=pathlib.Path, required=True)
    through.add_argument("--cursor", type=int, required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--root", type=pathlib.Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "append":
            snapshot = ""
            if arguments.snapshot_file is not None:
                snapshot = arguments.snapshot_file.read_text(encoding="utf-8")
            cursor = append_activity(
                arguments.root,
                kind=arguments.kind,
                source=arguments.source,
                detail=arguments.detail,
                snapshot=snapshot,
            )
            print(cursor)
        elif arguments.command == "report":
            result = record_assessment(arguments.root, _read_input(arguments.input))
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        elif arguments.command == "require-current":
            highwater, reported = require_current(arguments.root)
            print(json.dumps({"activity": highwater, "assessed": reported}, sort_keys=True))
        elif arguments.command == "require-through":
            reported = require_through(arguments.root, arguments.cursor)
            print(json.dumps({"required": arguments.cursor, "assessed": reported}, sort_keys=True))
        else:
            with _channel_lock(arguments.root) as directory:
                highwater = _validate_activity_rows(_read_jsonl(directory / "activity.jsonl"))
                reported = _validate_report_rows(
                    _read_jsonl(directory / "reports.jsonl"),
                    highwater=highwater,
                )
            print(
                json.dumps(
                    {"activity": highwater, "assessed": reported, "current": highwater == reported},
                    sort_keys=True,
                )
            )
    except (OSError, OrchestratorChannelError) as exc:
        print(f"orchestrator channel refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
