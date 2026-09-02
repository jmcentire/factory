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
from lane_dialogue import (  # noqa: E402
    LaneDialogueError,
    pending_questions,
)


class OrchestratorChannelError(RuntimeError):
    """The resident Orchestrator channel could not prove a safe result."""


_ACTIVITY_SCHEMA = "factory-orchestrator-activity/1"
_ASSESSMENT_SCHEMA = "factory-orchestrator-assessment/2"
_REPORT_SCHEMA = "factory-orchestrator-report/1"
_KIN_ID = re.compile(r"^[0-9a-f]{12}$")
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
_FALSE_CLOSE = re.compile(
    r"\b(?:run\s+(?:is\s+|[A-Za-z0-9._/-]+\s+is\s+)?|officially\s+)"
    r"(?:closed|complete|completed|done|finished)\b",
    re.IGNORECASE,
)
_MAX_JOURNAL_BYTES = 32 * 1024 * 1024
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
        if row.get("kind") not in _ACTIVITY_KINDS or row.get("source") not in _ACTIVITY_SOURCES:
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
    if source not in _ACTIVITY_SOURCES:
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


def _read_harness_state(root: pathlib.Path) -> str:
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
    return str(document["status"])


def _validate_assessment(
    value: object,
    *,
    highwater: int,
    current_harness_state: str | None = None,
    pending_lane_questions: Sequence[str] = (),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OrchestratorChannelError("assessment must be an object")
    expected = {
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
    if set(value) != expected or value.get("schema_version") != _ASSESSMENT_SCHEMA:
        raise OrchestratorChannelError("assessment has unknown or missing fields")
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
    if decision not in {"block", "no-op"}:
        raise OrchestratorChannelError("assessment decision must be block or no-op")
    should_block = (
        not value["direction_correct"]
        or not value["desirable_outcome"]
        or not value["advances_goal"]
        or not value["aligned"]
        or bool(findings)
        or bool(simplification_questions)
        or planning_mode == "clarify"
    )
    if should_block and decision != "block":
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
    if len(_canonical(value)) > _MAX_REPORT_BYTES:
        raise OrchestratorChannelError("assessment exceeds its byte ceiling")
    return dict(value)


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
        assessment = _validate_assessment(row.get("assessment"), highwater=highwater)
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
        assessment = _validate_assessment(
            value,
            highwater=highwater,
            current_harness_state=_read_harness_state(root),
            pending_lane_questions=pending,
        )
        report_path = directory / "reports.jsonl"
        existing = _read_jsonl(report_path)
        previous_cursor = _validate_report_rows(existing, highwater=highwater)
        cursor = int(assessment["through_cursor"])
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


def resident_mode(root: pathlib.Path) -> bool:
    try:
        metadata = json.loads((pathlib.Path(root) / "harness.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(metadata, dict) and metadata.get("orchestrator_mode") == "resident-monitoring"


def require_current(root: pathlib.Path) -> tuple[int, int]:
    """Refuse a resident-mode transition whose complete activity stream is unassessed."""

    root = pathlib.Path(root)
    if not resident_mode(root):
        return 0, 0
    with _channel_lock(root) as directory:
        highwater = _validate_activity_rows(_read_jsonl(directory / "activity.jsonl"))
        reported = _validate_report_rows(
            _read_jsonl(directory / "reports.jsonl"),
            highwater=highwater,
        )
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
    if not resident_mode(root):
        return 0
    with _channel_lock(root) as directory:
        highwater = _validate_activity_rows(_read_jsonl(directory / "activity.jsonl"))
        reported = _validate_report_rows(
            _read_jsonl(directory / "reports.jsonl"),
            highwater=highwater,
        )
    if cursor > highwater:
        raise OrchestratorChannelError("required cursor is beyond the activity journal")
    if reported < cursor:
        raise OrchestratorChannelError(
            f"resident Orchestrator has not assessed checkpoint {cursor}: assessed={reported}"
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
    append.add_argument("--source", choices=sorted(_ACTIVITY_SOURCES), required=True)
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
