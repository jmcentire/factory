#!/usr/bin/env python3
"""Bounded, opt-in Jev observations for the resident Orchestrator.

The dispatcher reserves a call durably before sending any bytes. A crashed call is
unavailable, never retried. Every activity occurrence has a receipt, including
unavailable evidence. Assessment consumes the exact receipts; scores grant nothing.
The subprocess boundary bounds total HTTP time, including a trickling response.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import stat
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

from jev_rules import MODEL_ID, RULEBOOK, band  # noqa: E402

RULES = {r["id"]: r for r in RULEBOOK if r["source"].startswith("msg-r2")}
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_INPUT = 128 * 1024
MAX_SOURCE = 16 * 1024
MAX_RESPONSE = 64 * 1024
TIMEOUT = 8


class ScreenError(RuntimeError):
    """Screening evidence is missing or invalid; no clean result may be inferred."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def configuration(calls: object) -> dict[str, Any]:
    if isinstance(calls, bool) or not isinstance(calls, int) or not 1 <= calls <= 1000:
        raise ScreenError("Jev call ceiling must be an integer from 1 through 1000")
    return {
        "schema_version": "factory-jev-screen/1",
        "max_calls": calls,
        "model": MODEL_ID,
        "rulebook_digest": digest(RULES),
    }


def check_config(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value != configuration(value.get("max_calls")):
        raise ScreenError("Jev configuration or retained rulebook does not match")
    return value


def read_evidence(root: pathlib.Path, relative: str) -> str:
    """Only bounded regular files beneath the retained run, never linked paths."""
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ScreenError("invalid retained evidence path")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            metadata = os.fstat(source)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_SOURCE:
                raise ScreenError("evidence is not a bounded non-empty regular file")
            raw = os.read(source, MAX_SOURCE + 1)
            if len(raw) > MAX_SOURCE:
                raise ScreenError("evidence exceeds source ceiling")
            text = raw.decode("utf-8")
            if not text.strip():
                raise ScreenError("evidence is empty")
            return text
        finally:
            os.close(source)
    finally:
        os.close(descriptor)


def project(root: pathlib.Path, activity: Mapping[str, Any]) -> dict[str, str]:
    state: dict[str, str] = {}
    if activity["kind"] == "pane_delta":
        state = {
            "lane": activity["source"],
            "pane_excerpt": activity["snapshot"],
            "pane_tail": activity["snapshot"],
            "line": activity["snapshot"],
        }
    # The Validator may attach handles to exact retained artifacts at a checkpoint.
    # Pane prose is never relabelled as a contract, brief, report or test file.
    handles = {"run_brief": "TASK.md", "testing_strategy": "artifacts/testing-strategy.md"}
    if activity["source"] == "validator" and activity["kind"] != "pane_delta":
        try:
            attachment = json.loads(activity["snapshot"])
        except ValueError:
            attachment = None
        if isinstance(attachment, dict) and set(attachment) == {"jev_evidence"}:
            supplied = attachment["jev_evidence"]
            fields = {f for r in RULES.values() for f in r["fields"]}
            if not isinstance(supplied, dict) or not set(supplied) <= fields:
                raise ScreenError("invalid Jev evidence handles")
            for field, path in supplied.items():
                if not isinstance(path, str) or not path.startswith("artifacts/"):
                    raise ScreenError("Jev evidence must name a retained artifacts/ file")
                handles[field] = path
    for field, path in handles.items():
        try:
            state[field] = read_evidence(root, path)
        except (OSError, UnicodeError, ScreenError):
            # Absence is represented by an unavailable rule, never an empty fact.
            pass
    return {k: v for k, v in state.items() if v.strip()}


def request(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one HTTP call under a total deadline, never via a shell."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise ScreenError("missing-key")
    try:
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--request"],
            input=canonical(payload),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ScreenError("deadline") from None
    if result.returncode or len(result.stdout) > MAX_RESPONSE:
        raise ScreenError("provider-unavailable")
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise ScreenError("invalid-response") from None
    if not isinstance(response, dict):
        raise ScreenError("invalid-response")
    return response


def evaluate(payload: dict[str, Any], call: Callable[[dict[str, Any]], dict[str, Any]]) -> dict:
    response = call(payload)
    answers = response.get("answers")
    if response.get("model") != MODEL_ID or not isinstance(answers, dict):
        raise ScreenError("model-or-answer-mismatch")
    if set(answers) != set(payload["questions"]):
        raise ScreenError("answer-set-mismatch")
    results = {}
    for rid, answer in answers.items():
        p = answer.get("noul") if isinstance(answer, dict) else None
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "noul"
            or isinstance(p, bool)
            or not isinstance(p, (float, int))
            or not math.isfinite(p)
            or not 0 <= p <= 1
        ):
            raise ScreenError("invalid-probability")
        results[rid] = {"band": band(p), "probability": p}
    return results


def _journal(rows: list[dict[str, Any]]) -> tuple[dict[int, dict], dict[int, dict]]:
    reservations: dict[int, dict] = {}
    receipts: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row.get("body"), dict) or row.get("digest") != digest(row["body"]):
            raise ScreenError("invalid screen journal digest")
        body = row["body"]
        check_config(body.get("config"))
        cursor = body.get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 1:
            raise ScreenError("invalid screen cursor")
        if body.get("kind") == "reserved":
            if (
                set(body) != {"kind", "cursor", "request_digest", "config", "state"}
                or cursor != len(receipts) + 1
                or cursor in reservations
                or cursor in receipts
            ):
                raise ScreenError("duplicate screen reservation")
            if body["request_digest"] != digest(
                {
                    "model": MODEL_ID,
                    "state": body["state"],
                    "questions": {
                        rid: r["rule"]
                        for rid, r in RULES.items()
                        if all(field in body["state"] for field in r["fields"])
                    },
                }
            ):
                raise ScreenError("reservation input mismatch")
            reservations[cursor] = body
        elif body.get("kind") == "receipt":
            if cursor != len(receipts) + 1:
                raise ScreenError("screen receipts are not contiguous")
            if set(body) != {
                "kind",
                "cursor",
                "activity_digest",
                "request_digest",
                "config",
                "state",
                "results",
            }:
                raise ScreenError("invalid screen receipt fields")
            if not isinstance(body["results"], dict) or set(body["results"]) != set(RULES):
                raise ScreenError("invalid screen result set")
            state = body["state"]
            if not isinstance(state, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in state.items()
            ):
                raise ScreenError("invalid retained screen state")
            questions = {
                rid: r["rule"]
                for rid, r in RULES.items()
                if all(field in state for field in r["fields"])
            }
            payload = {"model": MODEL_ID, "state": state, "questions": questions}
            if body["request_digest"] != digest(payload):
                raise ScreenError("screen request digest mismatch")
            if cursor in reservations and (
                reservations[cursor]["request_digest"] != body["request_digest"]
                or reservations[cursor]["config"] != body["config"]
            ):
                raise ScreenError("receipt does not match its reservation")
            for result in body["results"].values():
                if not isinstance(result, dict):
                    raise ScreenError("invalid screen result")
                if result.get("band") == "unavailable":
                    if set(result) != {"band", "reason"} or result["reason"] not in {
                        "missing-evidence",
                        "interrupted-call",
                        "call-ceiling",
                        "input-ceiling",
                        "provider-unavailable",
                    }:
                        raise ScreenError("invalid screen unavailability")
                else:
                    probability = result.get("probability")
                    if (
                        set(result) != {"band", "probability"}
                        or isinstance(probability, bool)
                        or not isinstance(probability, (int, float))
                        or not math.isfinite(probability)
                        or not 0 <= probability <= 1
                        or result["band"] != band(probability)
                    ):
                        raise ScreenError("invalid retained screen probability")
            receipts[cursor] = row
        else:
            raise ScreenError("unknown screen journal row")
    return reservations, receipts


def _screen_one(
    root: pathlib.Path, call: Callable[[dict[str, Any]], dict[str, Any]] | None = None
) -> int | None:
    # Share the channel's durable bounded writer and lock. Local import avoids a
    # cycle: assessment validates these receipts but never invokes the provider.
    from orchestrator_channel import (
        _append,
        _channel_lock,
        _read_harness,
        _read_jsonl,
        _validate_activity_rows,
    )

    with _channel_lock(root) as directory:
        config = check_config(_read_harness(root).get("jev_screen"))
        if config is None:
            return None
        activities = _read_jsonl(directory / "activity.jsonl")
        _validate_activity_rows(activities)
        journal = directory / "jev.jsonl"
        reservations, receipts = _journal(_read_jsonl(journal))
        # One call for this occurrence; every original activity stays visible.
        if len(receipts) >= len(activities):
            return len(receipts)
        activity = activities[len(receipts)]
        cursor = int(activity["cursor"])
        state = reservations[cursor]["state"] if cursor in reservations else project(root, activity)
        questions = {
            rid: r["rule"]
            for rid, r in RULES.items()
            if all(field in state for field in r["fields"])
        }
        payload = {"model": MODEL_ID, "state": state, "questions": questions}
        results = {
            rid: {"band": "unavailable", "reason": "missing-evidence"}
            for rid in RULES
            if rid not in questions
        }
        error = None
        if cursor in reservations:
            error = "interrupted-call"
        elif len(reservations) >= config["max_calls"]:
            error = "call-ceiling"
        elif len(canonical(payload)) > MAX_INPUT:
            error = "input-ceiling"
        elif questions:
            reservation = {
                "kind": "reserved",
                "cursor": cursor,
                "request_digest": digest(payload),
                "config": config,
                "state": state,
            }
            _append(journal, {"body": reservation, "digest": digest(reservation)})
            try:
                results.update(evaluate(payload, call or request))
            except (ScreenError, OSError, ValueError):
                # Exception messages and HTTP bodies may contain credentials or
                # reflected input. Only a stable classification reaches journals.
                error = "provider-unavailable"
        if error:
            results.update({rid: {"band": "unavailable", "reason": error} for rid in questions})
        body = {
            "kind": "receipt",
            "cursor": cursor,
            "activity_digest": digest(activity),
            "request_digest": digest(payload),
            "config": config,
            "state": state,
            "results": results,
        }
        _append(journal, {"body": body, "digest": digest(body)})
        return cursor


def screen_pending(
    root: pathlib.Path, call: Callable[[dict[str, Any]], dict[str, Any]] | None = None
) -> int | None:
    """At most four calls per poll (32 seconds); never filter the activity stream."""
    cursor = None
    for _ in range(4):
        cursor = _screen_one(root, call)
        if cursor is None:
            break
    return cursor


def validate_review(
    directory: pathlib.Path,
    activities: list[dict[str, Any]],
    config: object,
    previous: int,
    through: int,
    review: object,
    decision: str,
    findings: list[str],
) -> None:
    """Require consumption of each exact receipt, with no automatic clearance."""
    from orchestrator_channel import _read_jsonl

    checked = check_config(config)
    if checked is None:
        if review is not None:
            raise ScreenError("screen review supplied for a disabled run")
        return
    _, receipts = _journal(_read_jsonl(directory / "jev.jsonl"))
    expected = list(range(previous + 1, through + 1))
    if not isinstance(review, list) or len(review) != len(expected):
        raise ScreenError("assessment must review every new Jev receipt")
    for cursor, item in zip(expected, review, strict=True):
        row = receipts.get(cursor)
        if row is None:
            raise ScreenError("screen receipt is pending")
        body = row["body"]
        if (
            body["config"] != checked
            or body["activity_digest"] != digest(activities[cursor - 1])
            or set(body["results"]) != set(RULES)
        ):
            raise ScreenError("screen receipt does not bind this activity and rulebook")
        if (
            not isinstance(item, dict)
            or set(item) != {"cursor", "receipt_digest", "rules"}
            or item["cursor"] != cursor
            or item["receipt_digest"] != row["digest"]
        ):
            raise ScreenError("screen review does not bind the exact receipt")
        # Collapse missing context / service outages to one obligation per
        # occurrence instead of inventing seventeen independent findings.
        required = {
            rid for rid, result in body["results"].items() if result["band"] in {"yes", "escalate"}
        }
        if any(r["band"] == "unavailable" for r in body["results"].values()):
            required.add("unavailable")
        dispositions = item["rules"]
        if not isinstance(dispositions, dict) or set(dispositions) != required:
            raise ScreenError("screen findings or unavailable checks were not reviewed")
        for rid, disposition in dispositions.items():
            if (
                not isinstance(disposition, dict)
                or set(disposition) != {"decision", "basis"}
                or disposition["decision"] not in {"block", "dismiss"}
                or not isinstance(disposition["basis"], str)
                or not disposition["basis"].strip()
                or len(disposition["basis"].encode()) > 4096
            ):
                raise ScreenError("screen disposition needs a bounded evidence basis")
            if disposition["decision"] == "block" and (
                decision not in {"block", "halt"} or not findings
            ):
                raise ScreenError(f"upheld screen finding {rid} requires an adherence block")


def _http_request() -> None:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ScreenError("input ceiling")
    req = urllib.request.Request(
        ENDPOINT,
        data=raw,
        headers={
            "Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as response:
        data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ScreenError("response ceiling")
    sys.stdout.buffer.write(data)


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--request"]:
            raise ScreenError("this entry point is the bounded HTTP worker")
        _http_request()
    except Exception:
        # Never expose a key, provider body or reflected lane content on stderr.
        sys.exit(70)
