#!/usr/bin/env python3
"""Check the Validator's periodic debrief to the human: an assessment, never a status recap.

Founder direction (2026-10-16): the Orchestrator and the Validator assess the code and the
progress regularly; status reporting does not substitute for it. The Validator writes a debrief
at every slice boundary and at least every 60 minutes of run time, as a run artifact
(``artifacts/debriefs/<UTC stamp>.json`` beside a short ``.md``), so a collector can post it to
the human's surface (Herald) without reading the Validator's pane.

The invariant: **every debrief item is classified, and every verified item cites the work.**
What the lanes say is a claim; it goes under ``unverified`` with the check that would settle it.
A debrief that judges nothing (no verified and no unverified item) is a status recap and is
refused. The overall ``signal_class`` is never below its most urgent item, and a decision the
human owes is at least ``action-required``. The class classifies the fact; whether anyone is
paged is decided downstream.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

from orchestrator_channel import (  # noqa: E402
    _SIGNAL_RANK,
    OrchestratorChannelError,
    _bounded_text,
    _signal_class,
    _text_list,
    _work_refs,
)

SCHEMA = "factory-validator-debrief/1"
TRIGGERS = frozenset({"slice-boundary", "interval", "human-request"})
MAX_INTERVAL_MINUTES = 60
_MAX_BYTES = 64 * 1024
_FIELDS = {
    "schema_version", "recorded_at", "through_cursor", "trigger", "summary", "signal_class",
    "verified", "unverified", "risks", "decisions_owed",
}
_ITEMS = {
    "verified": {"item", "evidence", "signal_class"},
    "unverified": {"claim", "claimed_by", "next_check", "signal_class"},
    "risks": {"risk", "refs", "signal_class"},
    "decisions_owed": {"question", "signal_class"},
}


class DebriefError(ValueError):
    """The debrief is malformed, unclassified, or a status recap."""


def _items(value: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    items = value.get(name)
    if not isinstance(items, list) or len(items) > 64:
        raise DebriefError(f"{name} must be a bounded list")
    for item in items:
        if not isinstance(item, dict) or set(item) != _ITEMS[name]:
            raise DebriefError(f"{name} entry has unknown or missing fields")
    return items


def _recorded_at(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise DebriefError("recorded_at must be an ISO-8601 UTC time")
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise DebriefError("recorded_at must be an ISO-8601 UTC time") from exc
    if stamp.tzinfo is None:
        raise DebriefError("recorded_at must carry its UTC offset")
    return stamp


def check(value: object) -> dict[str, Any]:
    """Return the debrief if it is a classified, evidenced assessment; raise otherwise."""

    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise DebriefError("debrief has unknown or missing fields")
    if value["schema_version"] != SCHEMA:
        raise DebriefError(f"debrief schema_version must be {SCHEMA}")
    _recorded_at(value["recorded_at"])
    cursor = value["through_cursor"]
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise DebriefError("through_cursor must be a non-negative activity cursor")
    if value["trigger"] not in TRIGGERS:
        raise DebriefError("trigger must be slice-boundary, interval, or human-request")
    try:
        _bounded_text(value["summary"], field="summary")
        overall = _signal_class(value["signal_class"], field="signal_class")
        floor = "informative"
        for name in _ITEMS:
            for item in _items(value, name):
                item_class = _signal_class(item["signal_class"], field=f"{name} signal_class")
                if name == "verified":
                    _bounded_text(item["item"], field="verified item")
                    _work_refs(item["evidence"], field="verified evidence")
                elif name == "unverified":
                    for field in ("claim", "claimed_by", "next_check"):
                        _bounded_text(item[field], field=f"unverified {field}")
                elif name == "risks":
                    _bounded_text(item["risk"], field="risk")
                    if item["refs"]:
                        _work_refs(item["refs"], field="risk refs")
                    else:
                        _text_list(item["refs"], field="risk refs")
                else:
                    _bounded_text(item["question"], field="decisions_owed question")
                    if _SIGNAL_RANK[item_class] < _SIGNAL_RANK["action-required"]:
                        raise DebriefError("a decision the human owes is at least action-required")
                if _SIGNAL_RANK[item_class] > _SIGNAL_RANK[floor]:
                    floor = item_class
    except OrchestratorChannelError as exc:
        raise DebriefError(str(exc)) from exc
    if not value["verified"] and not value["unverified"]:
        raise DebriefError(
            "a debrief that judges nothing is a status recap: name what is verified, with "
            "evidence, or what is claimed and how it will be checked"
        )
    if _SIGNAL_RANK[overall] < _SIGNAL_RANK[floor]:
        raise DebriefError(f"debrief signal_class {overall} is below its items ({floor})")
    if len(json.dumps(value).encode("utf-8")) > _MAX_BYTES:
        raise DebriefError("debrief exceeds its byte ceiling")
    return dict(value)


def overdue(root: pathlib.Path, *, now: dt.datetime | None = None,
            max_minutes: int = MAX_INTERVAL_MINUTES) -> str | None:
    """Why the run owes a debrief now, or None. Only well-formed debriefs count."""

    now = now or dt.datetime.now(dt.UTC)
    latest: dt.datetime | None = None
    directory = pathlib.Path(root) / "artifacts" / "debriefs"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            debrief = check(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, DebriefError):
            continue
        stamp = _recorded_at(debrief["recorded_at"])
        latest = stamp if latest is None or stamp > latest else latest
    if latest is None:
        return "no well-formed debrief has been written"
    age = (now - latest).total_seconds() / 60
    if age > max_minutes:
        return f"the latest debrief is {int(age)} minutes old (limit {max_minutes})"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    checker = commands.add_parser("check", help="check one debrief file")
    checker.add_argument("path", type=pathlib.Path)
    due = commands.add_parser("overdue", help="exit 1 and say why when a debrief is owed")
    due.add_argument("--root", type=pathlib.Path, required=True)
    due.add_argument("--max-minutes", type=int, default=MAX_INTERVAL_MINUTES)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check":
            check(json.loads(arguments.path.read_text(encoding="utf-8")))
            print("validator-debrief: ok")
            return 0
        reason = overdue(arguments.root, max_minutes=arguments.max_minutes)
        if reason is None:
            print("validator-debrief: current")
            return 0
        print(f"validator-debrief: owed: {reason}")
        return 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, DebriefError) as exc:
        print(f"validator-debrief: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
