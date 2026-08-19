"""Closed, deterministic admission of every byte that may condition an agent run.

The capsule is provenance, not authority.  Authority remains in the signed phase artifacts,
externally pinned resume checkpoint, and the verified lifecycle ledger.  This module records
which already-classified bytes crossed the runtime boundary and refuses incomplete, ambiguous,
oversized, or trust-escalated dependency sets before a model can be invoked.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.schema import DocumentValidationError, validate_document

_MAX_TOTAL_BYTES = 5_242_880


@dataclass(frozen=True)
class DependencyRule:
    kind: str
    trust_class: str
    max_bytes: int

    def to_dict(self, dependency_id: str) -> dict[str, Any]:
        return {
            "dependency_id": dependency_id,
            "kind": self.kind,
            "trust_class": self.trust_class,
            "max_bytes": self.max_bytes,
        }


_TRUST_CEILINGS: Mapping[str, str] = {
    "target-state": "verified-state",
    "ledger-anchor": "verified-state",
    "phase-authority-references": "authority-reference",
    "phase-authority-artifact": "authority-reference",
    "task-input": "context",
    "lane-projection": "context",
    "role-primer": "context",
    "effective-directives": "context",
    "directive-readback": "context",
    "role-contract": "configuration-reference",
    "runner-manifest": "configuration-reference",
    "output-schema": "configuration-reference",
    "broker-registry": "configuration-reference",
    "resume-anchor": "authority-reference",
    "configuration-set": "configuration-reference",
    "state-qualification-observations": "configuration-reference",
    "state-qualification-report": "configuration-reference",
    "orchestrator-trigger": "untrusted-data",
    "orchestrator-task": "context",
    "orchestrator-phase-snapshot": "context",
    "receipt-tail": "context",
    "event-tail": "context",
    "minutes-tail": "untrusted-data",
    "directive-snapshot": "context",
    "run-projection": "verified-state",
    "harness-metadata": "context",
}


_PURPOSE_PROFILES: Mapping[str, Mapping[str, DependencyRule]] = {
    "lane-dispatch": {
        "target-state": DependencyRule("target-state", "verified-state", 1_048_576),
        "run-ledger-head": DependencyRule("ledger-anchor", "verified-state", 128),
        "phase-artifact-digests": DependencyRule(
            "phase-authority-references", "authority-reference", 16_384
        ),
        "phase-artifact-product-specification": DependencyRule(
            "phase-authority-artifact", "authority-reference", 1_048_576
        ),
        "phase-artifact-architecture": DependencyRule(
            "phase-authority-artifact", "authority-reference", 1_048_576
        ),
        "phase-artifact-operational-maturity": DependencyRule(
            "phase-authority-artifact", "authority-reference", 1_048_576
        ),
        "frozen-task": DependencyRule("task-input", "context", 2_097_152),
        "runner-projection": DependencyRule("lane-projection", "context", 1_500_000),
        "role-primer": DependencyRule("role-primer", "context", 262_144),
        "effective-directives": DependencyRule(
            "effective-directives", "context", 262_144
        ),
        "directive-readback": DependencyRule(
            "directive-readback", "context", 262_144
        ),
        "role-contract": DependencyRule(
            "role-contract", "configuration-reference", 262_144
        ),
        "runner-manifest": DependencyRule(
            "runner-manifest", "configuration-reference", 262_144
        ),
        "runner-output-schema": DependencyRule(
            "output-schema", "configuration-reference", 262_144
        ),
        "broker-registry": DependencyRule(
            "broker-registry", "configuration-reference", 524_288
        ),
        "resume-checkpoint": DependencyRule(
            "resume-anchor", "authority-reference", 1_048_576
        ),
        "resume-verification": DependencyRule(
            "resume-anchor", "authority-reference", 65_536
        ),
        "configuration-set": DependencyRule(
            "configuration-set", "configuration-reference", 262_144
        ),
        "state-qualification-observations": DependencyRule(
            "state-qualification-observations", "configuration-reference", 1_048_576
        ),
        "state-qualification-report": DependencyRule(
            "state-qualification-report", "configuration-reference", 262_144
        ),
    },
    "orchestrator-wake": {
        "trigger": DependencyRule("orchestrator-trigger", "untrusted-data", 16_384),
        "task": DependencyRule("orchestrator-task", "context", 65_536),
        "phase-artifacts": DependencyRule(
            "orchestrator-phase-snapshot", "context", 131_072
        ),
        "receipt-tail": DependencyRule("receipt-tail", "context", 65_536),
        "event-tail": DependencyRule("event-tail", "context", 65_536),
        "minutes-tail": DependencyRule("minutes-tail", "untrusted-data", 65_536),
        "active-directives": DependencyRule(
            "directive-snapshot", "context", 262_144
        ),
        "run-projection": DependencyRule("run-projection", "verified-state", 65_536),
        "harness-metadata": DependencyRule("harness-metadata", "context", 65_536),
    },
}


class StateAdmissionError(ValueError):
    """The closed dependency set could not be admitted without guessing."""

    def __init__(self, code: str, message: str, *, dependency_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.dependency_id = dependency_id
        self.receipt_retained = False
        self.receipt_attempted = False
        self.receipt_retention_error = ""


def profile_document(purpose: str) -> dict[str, Any]:
    try:
        profile = _PURPOSE_PROFILES[purpose]
    except KeyError as exc:
        raise StateAdmissionError("UNKNOWN_PURPOSE", f"unknown state purpose: {purpose!r}") from exc
    dependencies = [
        profile[dependency_id].to_dict(dependency_id)
        for dependency_id in sorted(profile)
    ]
    return {
        "schema_version": "factory-state-dependency-profile/1",
        "purpose": purpose,
        "dependencies": dependencies,
    }


def profile_digest(purpose: str) -> str:
    return digest_obj(profile_document(purpose))


def _canonical_entries(
    purpose: str,
    dependencies: Mapping[str, bytes],
) -> list[dict[str, Any]]:
    profile = _PURPOSE_PROFILES.get(purpose)
    if profile is None:
        raise StateAdmissionError("UNKNOWN_PURPOSE", f"unknown state purpose: {purpose!r}")
    expected = set(profile)
    supplied = set(dependencies)
    missing = sorted(expected - supplied)
    unknown = sorted(supplied - expected)
    if missing:
        raise StateAdmissionError(
            "MISSING_DEPENDENCY",
            f"state dependency set is missing {missing[0]!r}",
            dependency_id=missing[0],
        )
    if unknown:
        raise StateAdmissionError(
            "UNKNOWN_DEPENDENCY",
            f"state dependency set contains unknown member {unknown[0]!r}",
            dependency_id=unknown[0],
        )
    entries: list[dict[str, Any]] = []
    total = 0
    for dependency_id in sorted(profile):
        raw = dependencies[dependency_id]
        if not isinstance(raw, bytes):
            raise StateAdmissionError(
                "INVALID_DEPENDENCY",
                f"state dependency {dependency_id!r} is not bytes",
                dependency_id=dependency_id,
            )
        rule = profile[dependency_id]
        if _TRUST_CEILINGS.get(rule.kind) != rule.trust_class:
            raise StateAdmissionError(
                "TRUST_PROFILE_CONTRADICTION",
                f"state dependency {dependency_id!r} exceeds its code-owned trust ceiling",
                dependency_id=dependency_id,
            )
        if len(raw) > rule.max_bytes:
            raise StateAdmissionError(
                "OVERSIZED_DEPENDENCY",
                f"state dependency {dependency_id!r} exceeds {rule.max_bytes} bytes",
                dependency_id=dependency_id,
            )
        total += len(raw)
        if total > _MAX_TOTAL_BYTES:
            raise StateAdmissionError(
                "OVERSIZED_DEPENDENCY_SET",
                "state dependency set exceeds its total byte ceiling",
                dependency_id=dependency_id,
            )
        entries.append(
            {
                "dependency_id": dependency_id,
                "kind": rule.kind,
                "trust_class": rule.trust_class,
                "content_digest": digest_bytes(raw),
                "byte_count": len(raw),
            }
        )
    return entries


def derive_state_capsule(
    *,
    purpose: str,
    run_id: str,
    generation: int,
    role: str,
    target_state_digest: str,
    run_ledger_head: str,
    resume_checkpoint_digest: str,
    dependencies: Mapping[str, bytes],
) -> dict[str, Any]:
    """Derive one deterministic capsule from an exact, closed byte mapping."""

    entries = _canonical_entries(purpose, dependencies)
    document = {
        "schema_version": "factory-state-dependency-capsule/1",
        "purpose": purpose,
        "profile_digest": profile_digest(purpose),
        "run_id": run_id,
        "generation": generation,
        "role": role,
        "target_state_digest": target_state_digest,
        "run_ledger_head": run_ledger_head,
        "resume_checkpoint_digest": resume_checkpoint_digest,
        "dependencies": entries,
        "dependency_set_digest": digest_obj(entries),
    }
    try:
        validate_document("state-dependency-capsule", document)
    except DocumentValidationError as exc:
        raise StateAdmissionError("INVALID_CAPSULE", str(exc)) from exc
    return document


def verify_state_capsule(
    document: Mapping[str, Any],
    *,
    expected_purpose: str | None = None,
    expected_run_id: str | None = None,
    expected_generation: int | None = None,
    expected_role: str | None = None,
    expected_target_state_digest: str | None = None,
    expected_run_ledger_head: str | None = None,
    expected_resume_checkpoint_digest: str | None = None,
    expected_dependencies: Mapping[str, bytes] | None = None,
) -> None:
    try:
        snapshot = json.loads(
            json.dumps(document, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError, RecursionError, json.JSONDecodeError) as exc:
        raise StateAdmissionError(
            "INVALID_CAPSULE", "state capsule is not canonical JSON"
        ) from exc
    if not isinstance(snapshot, Mapping):
        raise StateAdmissionError("INVALID_CAPSULE", "state capsule must be an object")
    try:
        validate_document("state-dependency-capsule", snapshot)
    except DocumentValidationError as exc:
        raise StateAdmissionError("INVALID_CAPSULE", str(exc)) from exc
    purpose = str(snapshot["purpose"])
    if expected_purpose is not None and purpose != expected_purpose:
        raise StateAdmissionError("PURPOSE_MISMATCH", "state capsule has the wrong purpose")
    if snapshot["profile_digest"] != profile_digest(purpose):
        raise StateAdmissionError("PROFILE_MISMATCH", "state capsule profile is not current")
    entries = list(snapshot["dependencies"])
    if snapshot["dependency_set_digest"] != digest_obj(entries):
        raise StateAdmissionError(
            "DEPENDENCY_SET_MISMATCH", "state capsule dependency set digest differs"
        )
    expected_fields = {
        "run_id": expected_run_id,
        "generation": expected_generation,
        "role": expected_role,
        "target_state_digest": expected_target_state_digest,
        "run_ledger_head": expected_run_ledger_head,
        "resume_checkpoint_digest": expected_resume_checkpoint_digest,
    }
    for field, expected in expected_fields.items():
        if expected is not None and snapshot[field] != expected:
            raise StateAdmissionError(
                "SCOPE_MISMATCH", f"state capsule has wrong {field}"
            )
    entry_map = {str(entry["dependency_id"]): entry for entry in entries}
    if len(entry_map) != len(entries):
        raise StateAdmissionError(
            "DUPLICATE_DEPENDENCY", "state capsule repeats a dependency identity"
        )
    profile = _PURPOSE_PROFILES[purpose]
    if set(entry_map) != set(profile):
        raise StateAdmissionError(
            "MEMBERSHIP_MISMATCH", "state capsule membership differs from its closed profile"
        )
    for dependency_id, rule in profile.items():
        entry = entry_map[dependency_id]
        if entry["kind"] != rule.kind or entry["trust_class"] != rule.trust_class:
            raise StateAdmissionError(
                "TRUST_PROFILE_CONTRADICTION",
                f"state capsule misclassifies {dependency_id!r}",
                dependency_id=dependency_id,
            )
        if int(entry["byte_count"]) > rule.max_bytes:
            raise StateAdmissionError(
                "OVERSIZED_DEPENDENCY",
                f"state capsule member {dependency_id!r} exceeds its ceiling",
                dependency_id=dependency_id,
            )
    if expected_dependencies is not None:
        expected_entries = _canonical_entries(purpose, expected_dependencies)
        if entries != expected_entries:
            raise StateAdmissionError(
                "DEPENDENCY_BYTES_MISMATCH",
                "state capsule differs from the exact retained dependency bytes",
            )


def read_stable_regular_bytes(
    path: str | Path,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    """Read one opened regular file twice and reject unstable or changed bytes."""

    source = Path(path)
    # O_NONBLOCK is inert for regular files and prevents a hostile FIFO/device from
    # stalling the verifier before fstat can reject its type.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise StateAdmissionError(
            "DEPENDENCY_UNAVAILABLE",
            f"{label} could not be opened safely: {exc.strerror or type(exc).__name__}",
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise StateAdmissionError(
                "DEPENDENCY_NOT_REGULAR", f"{label} must be a regular file"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(max_bytes + 1)
            handle.seek(0)
            confirmed = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise StateAdmissionError(
            "DEPENDENCY_READ_FAILED",
            f"{label} could not be read safely: {exc.strerror or type(exc).__name__}",
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > max_bytes:
        raise StateAdmissionError(
            "OVERSIZED_DEPENDENCY", f"{label} exceeds {max_bytes} bytes"
        )
    if raw != confirmed:
        raise StateAdmissionError(
            "DEPENDENCY_CHANGED_DURING_READ", f"{label} changed while it was read"
        )
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or before.st_size != len(confirmed):
        raise StateAdmissionError(
            "DEPENDENCY_CHANGED_DURING_READ", f"{label} changed while it was read"
        )
    return confirmed


def dependency_rule(purpose: str, dependency_id: str) -> DependencyRule:
    try:
        return _PURPOSE_PROFILES[purpose][dependency_id]
    except KeyError as exc:
        raise StateAdmissionError(
            "UNKNOWN_DEPENDENCY",
            f"unknown {purpose!r} dependency {dependency_id!r}",
            dependency_id=dependency_id,
        ) from exc


__all__ = [
    "DependencyRule",
    "StateAdmissionError",
    "dependency_rule",
    "derive_state_capsule",
    "profile_digest",
    "profile_document",
    "read_stable_regular_bytes",
    "verify_state_capsule",
]
