"""Code-owned state-triggered obligations with durable point-for-point receipts.

This is the non-optional base catalog.  It does not invent product acceptance criteria; those
remain in the three human/Validator-ratified phase artifacts and the evidence plane.  It makes
the generic transition duties execute every time and leaves a content-addressed report that a
ledger re-derivation can reproduce without trusting the operator that requested the transition.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_obj
from factory_runtime.durability import DurabilityError, fsync_directory, fsync_directory_chain
from factory_runtime.schema import DocumentValidationError, validate_document

SET_KEY = "transition-obligation-set"
REPORT_KEY = "transition-obligation-report"


class TransitionObligationError(ValueError):
    """A lifecycle transition did not discharge its code-owned obligations."""


_BASE = (
    (
        "ledger-anchor",
        "ledger-prefix-binding-check",
        "The transition must bind a canonical verified lifecycle head selected for this attempt.",
    ),
    (
        "target-subject",
        "target-subject-digest-check",
        "The transition must preserve the content-addressed target-state, commit, and tree.",
    ),
)

_DESTINATION_OBLIGATIONS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "target-resolved": (
        (
            "stage-r-resolution",
            "stage-r-subject-resource-check",
            "The resolved target must bind its exact subject digest and resource head.",
        ),
    ),
    "intake": (
        (
            "stage-e-execution-authority",
            "stage-e-authority-binding-check",
            "Execution must bind canonical Stage-E request, receipt, genesis, and source digests.",
        ),
    ),
    "product-specification-ratified": (
        (
            "phase-human-validator-ratification",
            "phase-receipt-membership-check",
            "The product specification needs distinct human and Validator ratification receipts.",
        ),
    ),
    "architecture-ratified": (
        (
            "phase-human-validator-ratification",
            "phase-receipt-membership-check",
            "The architecture needs distinct human and Validator ratification receipts.",
        ),
    ),
    "operational-maturity-ratified": (
        (
            "phase-human-validator-ratification",
            "phase-receipt-membership-check",
            "The test and operational plan needs human and Validator ratification receipts.",
        ),
    ),
    "building": (
        (
            "external-resume-anchor",
            "external-checkpoint-binding-check",
            "Build dispatch must bind an externally anchored resume checkpoint.",
        ),
        (
            "generation-readiness",
            "generation-tuple-membership-check",
            "Build dispatch must freeze the complete generation tuple and bounded attempt.",
        ),
        (
            "existing-test-expectations",
            "test-change-authority-check",
            "Any existing-test expectation change needs one exact ruling signed by an "
            "enrolled human and distinct Validator.",
        ),
    ),
    "validating": (
        (
            "immutable-author-outputs",
            "snapshot-membership-check",
            "Coder and Tester outputs must be separately frozen before Validator execution.",
        ),
        (
            "tester-independence",
            "lane-identity-check",
            "Tester ownership and acceptance-test bytes must remain independent of the Coder.",
        ),
        (
            "existing-test-expectations",
            "test-change-authority-check",
            "Any existing-test expectation change needs one exact ruling signed by an "
            "enrolled human and distinct Validator.",
        ),
    ),
    "preview": (
        (
            "validator-adversarial-review",
            "validator-adversarial-review-membership-check",
            "Preview requires a complete immutable-subject code review over intent, tests, and "
            "the code-owned architecture and quality dimensions.",
        ),
        (
            "validator-evidence",
            "validator-evidence-membership-check",
            "Preview requires the exact candidate, tests, evidence bundle, and Validator envelope.",
        ),
        (
            "existing-test-expectations",
            "test-change-authority-check",
            "Any existing-test expectation change needs one exact ruling signed by an "
            "enrolled human and distinct Validator.",
        ),
    ),
    "human-approved": (
        (
            "human-candidate-approval",
            "human-identity-candidate-binding-check",
            "A distinct enrolled human must approve the exact candidate bytes.",
        ),
    ),
    "ci": (
        (
            "ci-evidence",
            "ci-evidence-binding-check",
            "CI admission must cite a content-addressed result for the approved candidate.",
        ),
    ),
    "promoted": (
        (
            "promotion-identity",
            "approved-candidate-equality",
            "The promoted artifact must be byte-for-byte the human-approved candidate.",
        ),
        (
            "resource-seal",
            "resource-ledger-seal-binding-check",
            "Promotion requires terminal accounting for every run-created resource.",
        ),
    ),
    "specification-defect": (
        (
            "specification-defect-handoff",
            "phase-invalidation-check",
            "A defect must identify the affected phase and invalidate dependent artifacts.",
        ),
    ),
    "blocked": (
        (
            "blocked-handoff",
            "bounded-blocked-handoff-check",
            "A stopped attempt must retain a typed reason and bounded continuation context.",
        ),
    ),
}

_ALLOWED_TRIGGER_PAIRS = frozenset(
    {
        ("target-resolution-authorized", "target-resolved"),
        ("target-resolved", "intake"),
        ("intake", "product-specification-ratified"),
        ("product-specification-ratified", "architecture-ratified"),
        ("product-specification-ratified", "specification-defect"),
        ("architecture-ratified", "operational-maturity-ratified"),
        ("architecture-ratified", "specification-defect"),
        ("operational-maturity-ratified", "building"),
        ("operational-maturity-ratified", "specification-defect"),
        ("building", "validating"),
        ("building", "specification-defect"),
        ("building", "blocked"),
        ("validating", "building"),
        ("validating", "preview"),
        ("validating", "specification-defect"),
        ("validating", "blocked"),
        ("preview", "human-approved"),
        ("preview", "specification-defect"),
        ("preview", "blocked"),
        ("human-approved", "ci"),
        ("human-approved", "specification-defect"),
        ("human-approved", "blocked"),
        ("ci", "promoted"),
        ("ci", "specification-defect"),
        ("ci", "blocked"),
        ("specification-defect", "product-specification-ratified"),
        ("specification-defect", "architecture-ratified"),
        ("specification-defect", "operational-maturity-ratified"),
        ("blocked", "building"),
        ("blocked", "blocked"),
        ("blocked", "specification-defect"),
    }
)

_CATALOG_BODY = {
    "schema_version": "factory-transition-obligation-catalog/1",
    "triggers": [
        {"from_state": source, "to_state": destination}
        for source, destination in sorted(_ALLOWED_TRIGGER_PAIRS)
    ],
    "base_obligations": [list(item) for item in _BASE],
    "destination_obligations": {
        key: [list(item) for item in value]
        for key, value in sorted(_DESTINATION_OBLIGATIONS.items())
    },
}
CATALOG_DIGEST = digest_obj(_CATALOG_BODY)

_DIGEST_PREFIX = "sha256:"
_PHASE_BY_DESTINATION = {
    "product-specification-ratified": "product-specification",
    "architecture-ratified": "architecture",
    "operational-maturity-ratified": "operational-maturity",
}
_GENERATION_KEYS = (
    "target-manifest-source",
    "pattern-catalog",
    "pattern-catalog-source",
    "build-plan",
    "build-plan-source",
    "build-input",
    "generation-readiness",
)


def assert_catalog_covers(allowed: Mapping[str, Sequence[str]]) -> None:
    """Fail if state.py grows a transition without an explicit obligation selector."""

    state_pairs = {
        (str(source), str(destination))
        for source, destinations in allowed.items()
        for destination in destinations
    }
    if state_pairs != set(_ALLOWED_TRIGGER_PAIRS):
        missing = sorted(state_pairs - _ALLOWED_TRIGGER_PAIRS)
        stale = sorted(_ALLOWED_TRIGGER_PAIRS - state_pairs)
        raise TransitionObligationError(
            f"transition obligation catalog drift (missing={missing}, stale={stale})"
        )


def _definitions(source: str, destination: str) -> tuple[tuple[str, str, str], ...]:
    if (source, destination) not in _ALLOWED_TRIGGER_PAIRS:
        raise TransitionObligationError(
            f"unknown state-triggered obligation selector: {source} -> {destination}"
        )
    selected = (*_BASE, *_DESTINATION_OBLIGATIONS[destination])
    ids = [item[0] for item in selected]
    if len(ids) != len(set(ids)):
        raise TransitionObligationError("selected obligation set contains duplicate ids")
    return selected


def require_transition_inputs(
    destination: str,
    supplied: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    """Require the minimum effect evidence before a satisfaction report can exist."""

    required: tuple[str, ...] = ()
    if destination == "building":
        required = ("resume-checkpoint", "generation-readiness")
    elif destination == "validating":
        required = (
            "candidate",
            "acceptance-tests",
            "coder-output-snapshot",
            "tester-output-snapshot",
        )
    elif destination == "preview":
        required = (
            "candidate",
            "acceptance-tests",
            "acceptance-obligation-report",
            "validator-review-subject",
            "validator-adversarial-review",
            "evidence-bundle",
            "evidence-envelope",
        )
    elif destination == "ci":
        required = ("ci-evidence",)
    missing = [key for key in required if not supplied.get(key)]
    if missing:
        raise TransitionObligationError(
            f"{destination} obligation(s) require artifact digest(s): {', '.join(missing)}"
        )
    changed_tests = payload.get("changed_existing_tests", [])
    if not isinstance(changed_tests, list):
        raise TransitionObligationError("changed_existing_tests must be an exact array")
    if changed_tests:
        required_test_change_authority = (
            "test-change-authorization",
            "test-change-authorization:human-receipt",
            "test-change-authorization:validator-receipt",
        )
        missing_test_change_authority = [
            key for key in required_test_change_authority if not supplied.get(key)
        ]
        if missing_test_change_authority:
            raise TransitionObligationError(
                "existing test changes require exact test-change authority artifact and "
                "receipt digest(s): " + ", ".join(missing_test_change_authority)
            )
        if any(not isinstance(item, str) or not item.strip() for item in changed_tests):
            raise TransitionObligationError("changed_existing_tests contains an invalid test id")
        if len(changed_tests) != len(set(changed_tests)):
            raise TransitionObligationError("changed_existing_tests contains duplicates")
    if destination == "blocked" and not str(payload.get("reason", "")).strip():
        raise TransitionObligationError("blocked transition requires a typed reason")


def _is_digest(value: Any) -> bool:
    text = str(value)
    return (
        text.startswith(_DIGEST_PREFIX)
        and len(text) == 71
        and all(character in "0123456789abcdef" for character in text[7:])
    )


def _require_digests(supplied: Mapping[str, Any], keys: Sequence[str]) -> bool:
    return all(_is_digest(supplied.get(key, "")) for key in keys)


def _verify_obligation(
    obligation_id: str,
    verifier_id: str,
    *,
    destination: str,
    prior_ledger_head: str,
    target_state_digest: str,
    target_state: Mapping[str, Any],
    phase_artifact_digests: Mapping[str, str],
    acceptance_obligation_catalog_digest: str,
    supplied: Mapping[str, Any],
    payload: Mapping[str, Any],
    approved_candidate_digest: str,
    implementer_identity: str,
    approver_identity: str,
) -> None:
    """Execute the closed verifier named by the selected catalog entry.

    These checks intentionally re-evaluate values rather than converting a prior admission into
    unconditional ``passed: true`` prose. Unknown verifier ids fail closed.
    """

    passed = False
    if verifier_id == "ledger-prefix-binding-check":
        passed = _is_digest(prior_ledger_head)
    elif verifier_id == "target-subject-digest-check":
        passed = (
            bool(target_state)
            and digest_obj(dict(target_state)) == target_state_digest
            and bool(str(target_state.get("resolved_commit", "")))
            and bool(str(target_state.get("resolved_tree", "")))
        )
    elif verifier_id == "stage-r-subject-resource-check":
        passed = (
            supplied.get("target-state") == target_state_digest
            and _is_digest(supplied.get("resource-ledger", ""))
            and target_state.get("resource_ledger_head") == supplied.get("resource-ledger")
        )
    elif verifier_id == "stage-e-authority-binding-check":
        passed = (
            _require_digests(
                supplied,
                ("execution-request", "execution-receipt", "authority-genesis", "source"),
            )
            and len(payload.get("authority_receipt_nonces", [])) == 1
        )
    elif verifier_id == "phase-receipt-membership-check":
        phase = _PHASE_BY_DESTINATION.get(destination, "")
        values = [
            supplied.get(phase, ""),
            supplied.get(f"{phase}:human-receipt", ""),
            supplied.get(f"{phase}:validator-receipt", ""),
        ]
        passed = (
            bool(phase)
            and all(_is_digest(value) for value in values)
            and len(set(values)) == 3
            and phase_artifact_digests.get(phase) == supplied.get(phase)
        )
    elif verifier_id == "external-checkpoint-binding-check":
        passed = (
            _is_digest(supplied.get("resume-checkpoint", ""))
            and bool(str(payload.get("resume_checkpoint_id", "")))
            and _is_digest(payload.get("anchored_run_ledger_head", ""))
            and isinstance(payload.get("anchored_run_ledger_length"), int)
            and int(payload["anchored_run_ledger_length"]) >= 1
            and _is_digest(acceptance_obligation_catalog_digest)
        )
    elif verifier_id == "generation-tuple-membership-check":
        passed = (
            _require_digests(supplied, _GENERATION_KEYS)
            and isinstance(payload.get("attempt_number"), int)
            and isinstance(payload.get("attempt_limit"), int)
            and 1 <= int(payload["attempt_number"]) <= int(payload["attempt_limit"])
        )
    elif verifier_id == "test-change-authority-check":
        changed = payload.get("changed_existing_tests", [])
        passed = isinstance(changed, list) and (
            not changed
            or (
                _require_digests(
                    supplied,
                    (
                        "test-change-authorization",
                        "test-change-authorization:human-receipt",
                        "test-change-authorization:validator-receipt",
                    ),
                )
                and len(
                    {
                        supplied.get("test-change-authorization", ""),
                        supplied.get("test-change-authorization:human-receipt", ""),
                        supplied.get("test-change-authorization:validator-receipt", ""),
                    }
                )
                == 3
                and all(isinstance(item, str) and item.strip() for item in changed)
                and len(changed) == len(set(changed))
            )
        )
    elif verifier_id == "snapshot-membership-check":
        passed = _require_digests(
            supplied,
            (
                "candidate",
                "acceptance-tests",
                "coder-output-snapshot",
                "tester-output-snapshot",
            ),
        )
    elif verifier_id == "lane-identity-check":
        tester_identity = str(payload.get("tester_identity", ""))
        passed = bool(tester_identity and implementer_identity) and (
            tester_identity != implementer_identity
        )
    elif verifier_id == "validator-evidence-membership-check":
        passed = _require_digests(
            supplied,
            (
                "candidate",
                "acceptance-tests",
                "acceptance-obligation-report",
                "evidence-bundle",
                "evidence-envelope",
            ),
        )
    elif verifier_id == "validator-adversarial-review-membership-check":
        passed = _require_digests(
            supplied,
            ("validator-review-subject", "validator-adversarial-review"),
        )
    elif verifier_id == "human-identity-candidate-binding-check":
        passed = (
            bool(approver_identity and implementer_identity)
            and approver_identity != implementer_identity
            and _is_digest(supplied.get("candidate", ""))
        )
    elif verifier_id == "ci-evidence-binding-check":
        passed = _is_digest(supplied.get("ci-evidence", ""))
    elif verifier_id == "approved-candidate-equality":
        passed = (
            _is_digest(approved_candidate_digest)
            and supplied.get("promoted-artifact") == approved_candidate_digest
        )
    elif verifier_id == "resource-ledger-seal-binding-check":
        passed = _require_digests(supplied, ("resource-ledger", "resource-ledger-seal"))
    elif verifier_id == "phase-invalidation-check":
        passed = str(payload.get("phase", "")) in set(_PHASE_BY_DESTINATION.values())
    elif verifier_id == "bounded-blocked-handoff-check":
        passed = bool(str(payload.get("reason", "")).strip())
    else:
        raise TransitionObligationError(f"unknown transition verifier id: {verifier_id}")
    if not passed:
        raise TransitionObligationError(
            f"obligation {obligation_id!r} failed verifier {verifier_id!r}"
        )


def _context(
    *,
    target_state: Mapping[str, Any],
    target_state_digest: str,
    supplied: Mapping[str, Any],
    payload: Mapping[str, Any],
    approved_candidate_digest: str,
    recorded_at: int,
    selector_id: str,
    prior_ledger_head: str,
) -> dict[str, Any]:
    def first_digest(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.startswith("sha256:"):
                return value
        return ""

    return {
        "target_state_digest": target_state_digest,
        "resolved_commit": str(target_state.get("resolved_commit", "")),
        "resolved_tree": str(target_state.get("resolved_tree", "")),
        "candidate_digest": first_digest(
            supplied.get("candidate"),
            supplied.get("promoted-artifact"),
            approved_candidate_digest,
        ),
        "diff_digest": first_digest(supplied.get("candidate-diff"), payload.get("diff_digest")),
        "command_digest": first_digest(payload.get("command_digest")),
        "test_id": str(payload.get("test_id", "")),
        "test_family": str(payload.get("test_family", "")),
        "configuration_digest": first_digest(
            supplied.get("runner-configuration"), payload.get("configuration_digest")
        ),
        "environment_digest": first_digest(payload.get("environment_digest")),
        "output_digest": first_digest(
            supplied.get("evidence-bundle"),
            supplied.get("ci-evidence"),
            payload.get("output_digest"),
        ),
        "recorded_at": recorded_at,
        "idempotency_key": digest_obj(
            {
                "selector_id": selector_id,
                "prior_ledger_head": prior_ledger_head,
                "payload": dict(payload),
                "artifacts": dict(sorted(supplied.items())),
            }
        ),
    }


def derive_transition_obligations(
    *,
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    prior_ledger_head: str,
    target_state_digest: str,
    target_state: Mapping[str, Any],
    phase_artifact_digests: Mapping[str, str],
    supplied_artifact_digests: Mapping[str, Any],
    payload: Mapping[str, Any],
    approved_candidate_digest: str,
    recorded_at: int,
    acceptance_obligation_catalog_digest: str = "",
    implementer_identity: str = "",
    verifier_identity: str = "",
    approver_identity: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select, verify, and render the exact obligations for one transition attempt."""

    definitions = _definitions(source, destination)
    require_transition_inputs(destination, supplied_artifact_digests, payload)
    selector_id = f"{source}--{destination}"
    set_document = {
        "schema_version": "factory-transition-obligation-set/1",
        "catalog_digest": CATALOG_DIGEST,
        "selector_id": selector_id,
        "run_id": run_id,
        "generation": generation,
        "from_state": source,
        "to_state": destination,
        "prior_ledger_head": prior_ledger_head,
        "target_state_digest": target_state_digest,
        "phase_artifact_digests": dict(sorted(phase_artifact_digests.items())),
        "acceptance_obligation_catalog_digest": acceptance_obligation_catalog_digest,
        "obligations": [
            {"obligation_id": item[0], "verifier_id": item[1], "criterion": item[2]}
            for item in definitions
        ],
    }
    try:
        validate_document("transition-obligation-set", set_document)
    except DocumentValidationError as exc:
        raise TransitionObligationError(str(exc)) from exc
    set_digest = digest_obj(set_document)
    common = _context(
        target_state=target_state,
        target_state_digest=target_state_digest,
        supplied=supplied_artifact_digests,
        payload=payload,
        approved_candidate_digest=approved_candidate_digest,
        recorded_at=recorded_at,
        selector_id=selector_id,
        prior_ledger_head=prior_ledger_head,
    )
    results = []
    for obligation_id, verifier_id, criterion in definitions:
        _verify_obligation(
            obligation_id,
            verifier_id,
            destination=destination,
            prior_ledger_head=prior_ledger_head,
            target_state_digest=target_state_digest,
            target_state=target_state,
            phase_artifact_digests=phase_artifact_digests,
            acceptance_obligation_catalog_digest=(acceptance_obligation_catalog_digest),
            supplied=supplied_artifact_digests,
            payload=payload,
            approved_candidate_digest=approved_candidate_digest,
            implementer_identity=implementer_identity,
            approver_identity=approver_identity,
        )
        observations = {
            "prior_ledger_head": prior_ledger_head,
            "artifact_digests": dict(sorted(supplied_artifact_digests.items())),
            "payload_digest": digest_obj(dict(payload)),
            "phase_artifact_digests": dict(sorted(phase_artifact_digests.items())),
            "changed_existing_tests": list(payload.get("changed_existing_tests", [])),
            "implementer_identity": implementer_identity,
            "verifier_identity": verifier_identity,
            "approver_identity": approver_identity,
        }
        if obligation_id == "blocked-handoff":
            observations["reason"] = str(payload.get("reason", ""))
        evidence_body = {
            "obligation_id": obligation_id,
            "verifier_id": verifier_id,
            "criterion": criterion,
            "context": common,
            "observations": observations,
        }
        results.append(
            {
                "obligation_id": obligation_id,
                "verifier_id": verifier_id,
                "criterion": criterion,
                "passed": True,
                "observations": observations,
                "evidence_digest": digest_obj(evidence_body),
            }
        )
    report_document = {
        "schema_version": "factory-transition-obligation-report/1",
        "run_id": run_id,
        "generation": generation,
        "obligation_set_digest": set_digest,
        "selector_id": selector_id,
        "from_state": source,
        "to_state": destination,
        **common,
        "results": results,
        "satisfied": True,
    }
    try:
        validate_document("transition-obligation-report", report_document)
    except DocumentValidationError as exc:
        raise TransitionObligationError(str(exc)) from exc
    return set_document, report_document


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    """Open a retained subject once and read only the regular inode that was checked."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TransitionObligationError(f"retained {label} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TransitionObligationError(f"retained {label} is not regular")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sync_directory(path: Path) -> None:
    try:
        fsync_directory(path)
    except DurabilityError as exc:
        raise TransitionObligationError(str(exc)) from exc


def _sync_evidence_directories(path: Path) -> None:
    """Commit both the file name and its content-addressed directory name."""

    _sync_directory(path.parent)
    _sync_directory(path.parent.parent)


def _sync_identical_evidence(path: Path, content: bytes) -> bool:
    """Stable-read and fsync the exact preinstalled evidence before ledger admission."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != len(content):
            return False
        chunks: list[bytes] = []
        remaining = len(content) + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if not stable or b"".join(chunks) != content:
            return False
        os.fsync(descriptor)
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise TransitionObligationError(
                "obligation evidence changed while identical bytes were retained"
            )
    except OSError as exc:
        raise TransitionObligationError(f"obligation evidence is unreadable: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _sync_evidence_directories(path)
    return True


def _write_once_or_identical(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".obligation-", dir=path.parent)
    installed_identity: tuple[int, int] | None = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        source = os.lstat(temporary)
        if not stat.S_ISREG(source.st_mode):
            raise TransitionObligationError("obligation evidence temporary is not regular")
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            if not _sync_identical_evidence(path, content):
                raise TransitionObligationError(
                    "obligation evidence address contains different bytes"
                ) from exc
            return
        installed = os.lstat(path)
        installed_identity = (installed.st_dev, installed.st_ino)
        if installed_identity != (source.st_dev, source.st_ino):
            raise TransitionObligationError("installed obligation evidence is not the staged inode")
        try:
            _sync_evidence_directories(path)
        except (OSError, TransitionObligationError):
            try:
                current = os.lstat(path)
                if (current.st_dev, current.st_ino) == installed_identity:
                    os.unlink(path)
                    try:
                        _sync_directory(path.parent)
                    except TransitionObligationError:
                        pass
            except OSError:
                pass
            raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def retain_transition_obligations(
    run_dir: str | Path,
    set_document: Mapping[str, Any],
    report_document: Mapping[str, Any],
) -> tuple[str, str]:
    """Persist content-addressed set/report bytes before the ledger spends their digests."""

    set_digest = digest_obj(dict(set_document))
    report_digest = digest_obj(dict(report_document))
    root = (
        Path(run_dir) / "evidence" / "transition-obligations" / set_digest.removeprefix("sha256:")
    )
    _write_once_or_identical(root / "set.json", _canonical_bytes(set_document))
    _write_once_or_identical(
        root / f"{report_digest.removeprefix('sha256:')}.report.json",
        _canonical_bytes(report_document),
    )
    try:
        fsync_directory_chain(root, through=Path(run_dir))
    except DurabilityError as exc:
        raise TransitionObligationError(str(exc)) from exc
    return set_digest, report_digest


def verify_retained_transition_obligations(
    run_dir: str | Path,
    *,
    expected_set: Mapping[str, Any],
    expected_report: Mapping[str, Any],
    set_digest: str,
    report_digest: str,
) -> None:
    """Reopen the exact retained bytes and compare them with a fresh derivation."""

    if digest_obj(dict(expected_set)) != set_digest:
        raise TransitionObligationError("ledger obligation-set digest does not re-derive")
    if digest_obj(dict(expected_report)) != report_digest:
        raise TransitionObligationError("ledger obligation-report digest does not re-derive")
    root = (
        Path(run_dir) / "evidence" / "transition-obligations" / set_digest.removeprefix("sha256:")
    )
    paths = (
        (root / "set.json", expected_set, "obligation set"),
        (
            root / f"{report_digest.removeprefix('sha256:')}.report.json",
            expected_report,
            "obligation report",
        ),
    )
    for path, expected, label in paths:
        content = _read_regular_bytes(path, label=label)
        if content != _canonical_bytes(expected):
            raise TransitionObligationError(f"retained {label} differs from fresh derivation")


__all__ = [
    "CATALOG_DIGEST",
    "REPORT_KEY",
    "SET_KEY",
    "TransitionObligationError",
    "assert_catalog_covers",
    "derive_transition_obligations",
    "require_transition_inputs",
    "retain_transition_obligations",
    "verify_retained_transition_obligations",
]
