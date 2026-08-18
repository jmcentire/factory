#!/usr/bin/env python3
"""Verify the bounded receipt that terminally disables one legacy harness.

The receipt is not authority to close or clean anything.  It only proves that an explicit,
human-named cutover record binds the exact still-open v1 harness bytes for this run.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import unicodedata
from collections.abc import Mapping
from typing import Any

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HUMAN = re.compile(r"^human:[A-Za-z0-9._-]+$")
_RECEIPT_FIELDS = {
    "schema_version",
    "run_id",
    "target_state_digest",
    "legacy_harness_source_digest",
    "legacy_schema_version",
    "disposition",
    "replacement_schema_version",
    "actor",
    "reason",
    "created_at",
}


def _contains_format_or_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


class LegacyAbandonmentError(ValueError):
    """A legacy-abandonment marker is absent, malformed, or unbound."""


def _stable_regular_bytes(path: pathlib.Path, *, label: str, limit: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise LegacyAbandonmentError(f"{label} is unavailable or unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LegacyAbandonmentError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            first = stream.read(limit + 1)
            stream.seek(0)
            second = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise LegacyAbandonmentError(f"{label} could not be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
    if (
        len(second) > limit
        or first != second
        or identity(before) != identity(after)
        or before.st_size != len(second)
    ):
        raise LegacyAbandonmentError(f"{label} changed or exceeded its byte ceiling")
    return second


def _object(raw: bytes, *, label: str) -> Mapping[str, Any]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacyAbandonmentError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, Mapping):
        raise LegacyAbandonmentError(f"{label} must be a JSON object")
    return document


def verify_legacy_abandonment(
    harness_path: str | pathlib.Path,
    receipt_path: str | pathlib.Path,
    *,
    run_id: str,
) -> Mapping[str, Any]:
    """Return a verified receipt or fail closed without treating its name as proof."""

    harness_raw = _stable_regular_bytes(
        pathlib.Path(harness_path), label="legacy harness metadata", limit=1_048_576
    )
    receipt_raw = _stable_regular_bytes(
        pathlib.Path(receipt_path), label="legacy abandonment receipt", limit=16_384
    )
    harness = _object(harness_raw, label="legacy harness metadata")
    receipt = _object(receipt_raw, label="legacy abandonment receipt")
    if set(receipt) != _RECEIPT_FIELDS:
        raise LegacyAbandonmentError("legacy abandonment receipt has an open or incomplete shape")
    target_digest = harness.get("target_state_digest")
    if (
        harness.get("schema_version") != "factory-harness/1"
        or harness.get("status") != "open"
        or harness.get("run_id") != run_id
        or not isinstance(target_digest, str)
        or _DIGEST.fullmatch(target_digest) is None
    ):
        raise LegacyAbandonmentError("legacy harness metadata is not an open bound v1 run")
    expected = {
        "schema_version": "factory-legacy-harness-abandonment/1",
        "run_id": run_id,
        "target_state_digest": target_digest,
        "legacy_harness_source_digest": "sha256:"
        + hashlib.sha256(harness_raw).hexdigest(),
        "legacy_schema_version": "factory-harness/1",
        "disposition": "abandoned-unqualified",
        "replacement_schema_version": "factory-harness/2",
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise LegacyAbandonmentError(f"legacy abandonment receipt has wrong {field}")
    actor = receipt.get("actor")
    reason = receipt.get("reason")
    if not isinstance(actor, str) or _HUMAN.fullmatch(actor) is None:
        raise LegacyAbandonmentError("legacy abandonment receipt has no human-shaped actor")
    if (
        not isinstance(reason, str)
        or not reason
        or len(reason.encode("utf-8")) > 4096
        or _contains_format_or_control(reason)
    ):
        raise LegacyAbandonmentError("legacy abandonment receipt has an invalid reason")
    created_at = receipt.get("created_at")
    try:
        timestamp = datetime.datetime.fromisoformat(str(created_at))
    except ValueError as exc:
        raise LegacyAbandonmentError("legacy abandonment receipt has an invalid timestamp") from exc
    if timestamp.tzinfo is None:
        raise LegacyAbandonmentError("legacy abandonment receipt timestamp is not timezone-aware")
    return dict(receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--run", required=True)
    arguments = parser.parse_args()
    try:
        document = verify_legacy_abandonment(
            arguments.harness,
            arguments.receipt,
            run_id=arguments.run,
        )
    except LegacyAbandonmentError as exc:
        parser.exit(1, f"legacy abandonment: {exc}\n")
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
