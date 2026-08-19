"""Closed Validator adversarial-review subject, report, and durable retention.

The reviewer remains a Validator activity, not a fourth standing role.  The host supplies an
immutable subject that names every authoritative input, then verifies that the report covers the
code-owned lens set and cites exact bytes from those inputs.  This module proves subject binding,
coverage, and disposition mechanics; it does not pretend to prove that a model's semantic judgment
is correct.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.snapshot import SnapshotError, tree_digest

REVIEW_SUBJECT_ARTIFACT_KEY = "validator-review-subject"
REVIEW_REPORT_ARTIFACT_KEY = "validator-adversarial-review"
REVIEW_PROTOCOL_ID = "factory-validator-adversarial-review/1"
REQUIRED_REVIEW_DIMENSIONS = (
    "intent-conformance",
    "architecture",
    "redundancy",
    "clarity",
    "separation-of-concerns",
    "test-adequacy",
    "correctness-and-failure",
    "scope-control",
)
REQUIRED_COMPLETENESS_CHECKS = (
    "dimension-coverage",
    "subject-binding",
    "test-evidence",
    "failure-mode-coverage",
    "change-reach",
    "provider-completion",
    "finding-refutation",
)
_PROTOCOL_BODY = {
    "protocol_id": REVIEW_PROTOCOL_ID,
    "report_schema_version": "factory-validator-adversarial-review/1",
    "required_dimensions": list(REQUIRED_REVIEW_DIMENSIONS),
    "required_completeness_checks": list(REQUIRED_COMPLETENESS_CHECKS),
    "finding_identity": "content-addressed-before-refutation",
    "clean_rule": "all-dimensions-and-completeness-completed-no-surviving-findings",
}
REVIEW_PROTOCOL_DIGEST = digest_obj(_PROTOCOL_BODY)
_MAX_REVIEW_BYTES = 4 * 1024 * 1024


class AdversarialReviewError(ValueError):
    """Validator review evidence is missing, stale, malformed, or non-reproducible."""


@dataclass(frozen=True)
class VerifiedAdversarialReview:
    subject: Mapping[str, Any]
    report: Mapping[str, Any]
    subject_digest: str
    report_digest: str

    @property
    def verdict(self) -> str:
        return str(self.report["verdict"])

    @property
    def passed(self) -> bool:
        return self.verdict == "CLEAN_QUALIFIED"


def canonical_document_bytes(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def build_validator_review_subject(
    *,
    run_id: str,
    generation: int,
    target_digest: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    reviewer_identity: str,
    build_input_digest: str,
    pattern_catalog_digest: str,
    pattern_catalog_source_digest: str,
    build_plan_digest: str,
    build_plan_source_digest: str,
    phase_artifact_digests: Mapping[str, str],
    acceptance_obligation_catalog_digest: str,
    acceptance_obligation_catalog_source_digest: str,
    candidate_digest: str,
    acceptance_tests_digest: str,
    coder_output_snapshot_digest: str,
    tester_output_snapshot_digest: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
) -> dict[str, Any]:
    """Compile the exact immutable inputs the Validator must review."""

    document = {
        "schema_version": "factory-validator-review-subject/1",
        "protocol": {
            "protocol_id": REVIEW_PROTOCOL_ID,
            "protocol_digest": REVIEW_PROTOCOL_DIGEST,
            "report_schema_version": "factory-validator-adversarial-review/1",
            "required_dimensions": list(REQUIRED_REVIEW_DIMENSIONS),
            "required_completeness_checks": list(REQUIRED_COMPLETENESS_CHECKS),
        },
        "run_id": run_id,
        "generation": generation,
        "target_digest": target_digest,
        "target_state_digest": target_state_digest,
        "resolved_commit": resolved_commit,
        "resolved_tree": resolved_tree,
        "reviewer_identity": reviewer_identity,
        "artifacts": {
            "build-input": build_input_digest,
            "pattern-catalog": pattern_catalog_digest,
            "pattern-catalog-source": pattern_catalog_source_digest,
            "build-plan": build_plan_digest,
            "build-plan-source": build_plan_source_digest,
            "product-specification": str(phase_artifact_digests.get("product-specification", "")),
            "architecture": str(phase_artifact_digests.get("architecture", "")),
            "operational-maturity": str(
                phase_artifact_digests.get("operational-maturity", "")
            ),
            "acceptance-obligation-catalog": acceptance_obligation_catalog_digest,
            "acceptance-obligation-catalog-source": (
                acceptance_obligation_catalog_source_digest
            ),
            "candidate": candidate_digest,
            "acceptance-tests": acceptance_tests_digest,
            "coder-output-snapshot": coder_output_snapshot_digest,
            "tester-output-snapshot": tester_output_snapshot_digest,
        },
        "validator_execution": {
            "command_digest": command_digest,
            "configuration_digest": configuration_digest,
            "environment_digest": environment_digest,
        },
    }
    try:
        validate_document("validator-review-subject", document)
    except DocumentValidationError as exc:
        raise AdversarialReviewError(str(exc)) from exc
    return document


def _stable_read(path: Path, *, label: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AdversarialReviewError(f"{label} is unreadable: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AdversarialReviewError(f"{label} is not a regular file")
        if before.st_size > _MAX_REVIEW_BYTES:
            raise AdversarialReviewError(f"{label} exceeds the review evidence byte limit")
        chunks: list[bytes] = []
        remaining = _MAX_REVIEW_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
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
        if identity_before != identity_after:
            raise AdversarialReviewError(f"{label} changed while it was read")
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise AdversarialReviewError(f"{label} pathname changed while it was read")
        data = b"".join(chunks)
        if len(data) != after.st_size:
            raise AdversarialReviewError(f"{label} was not read completely")
        return data
    except OSError as exc:
        raise AdversarialReviewError(f"{label} could not be read safely: {exc}") from exc
    finally:
        os.close(descriptor)


def load_canonical_review_report(path: str | Path) -> dict[str, Any]:
    """Read one report without following links and require canonical retained bytes."""

    source = Path(path)
    data = _stable_read(source, label="Validator adversarial-review report")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdversarialReviewError(
            f"Validator adversarial-review report is invalid: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise AdversarialReviewError("Validator adversarial-review report must be an object")
    try:
        validate_document("validator-adversarial-review", raw)
    except DocumentValidationError as exc:
        raise AdversarialReviewError(str(exc)) from exc
    if data != canonical_document_bytes(raw):
        raise AdversarialReviewError("Validator adversarial-review report is not canonical JSON")
    return {str(key): value for key, value in raw.items()}


def _relative_evidence_path(value: object) -> PurePosixPath:
    text = str(value)
    candidate = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or candidate.is_absolute()
        or text != candidate.as_posix()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise AdversarialReviewError(f"review evidence has unsafe path: {text!r}")
    return candidate


def _evidence_file(
    reference: Mapping[str, Any],
    *,
    implementation_root: Path,
    tests_root: Path,
    build_input_path: Path,
    pattern_catalog_path: Path,
    build_plan_path: Path,
    acceptance_catalog_path: Path,
    acceptance_observations_path: Path,
) -> Path:
    source = str(reference["source"])
    relative = _relative_evidence_path(reference["path"])
    if source == "implementation":
        root = implementation_root.resolve()
        candidate = root.joinpath(*relative.parts)
    elif source == "acceptance-tests":
        root = tests_root.resolve()
        candidate = root.joinpath(*relative.parts)
    elif source == "build-input":
        if relative.as_posix() != "build-input.json":
            raise AdversarialReviewError("build-input evidence must cite build-input.json")
        return build_input_path
    elif source == "pattern-catalog":
        if relative.as_posix() != "pattern-catalog.json":
            raise AdversarialReviewError("pattern-catalog evidence must cite pattern-catalog.json")
        return pattern_catalog_path
    elif source == "build-plan":
        if relative.as_posix() != "build-plan.json":
            raise AdversarialReviewError("build-plan evidence must cite build-plan.json")
        return build_plan_path
    elif source == "acceptance-obligation-catalog":
        if relative.as_posix() != "acceptance-obligation-catalog.json":
            raise AdversarialReviewError(
                "acceptance catalog evidence must cite its canonical filename"
            )
        return acceptance_catalog_path
    elif source == "acceptance-observations":
        if relative.as_posix() != "acceptance-obligation-observations.json":
            raise AdversarialReviewError(
                "acceptance observation evidence must cite its canonical filename"
            )
        return acceptance_observations_path
    else:  # Schema validation should make this unreachable.
        raise AdversarialReviewError(f"unknown review evidence source: {source}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise AdversarialReviewError("review evidence escapes its immutable subject root")
    return candidate


def _verify_evidence_reference(
    reference: Mapping[str, Any],
    *,
    implementation_root: Path,
    tests_root: Path,
    build_input_path: Path,
    pattern_catalog_path: Path,
    build_plan_path: Path,
    acceptance_catalog_path: Path,
    acceptance_observations_path: Path,
) -> None:
    path = _evidence_file(
        reference,
        implementation_root=implementation_root,
        tests_root=tests_root,
        build_input_path=build_input_path,
        pattern_catalog_path=pattern_catalog_path,
        build_plan_path=build_plan_path,
        acceptance_catalog_path=acceptance_catalog_path,
        acceptance_observations_path=acceptance_observations_path,
    )
    data = _stable_read(path, label=f"review evidence {reference['source']}:{reference['path']}")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdversarialReviewError("review evidence must be UTF-8 line-addressable text") from exc
    lines = data.splitlines(keepends=True)
    start = int(reference["start_line"])
    end = int(reference["end_line"])
    if start > end or end > len(lines):
        raise AdversarialReviewError(
            f"review evidence line range {start}-{end} is outside {reference['path']}"
        )
    excerpt = b"".join(lines[start - 1 : end])
    if digest_bytes(excerpt) != reference["excerpt_digest"]:
        raise AdversarialReviewError(
            f"review evidence excerpt digest does not match {reference['path']}:{start}-{end}"
        )


def _all_evidence(report: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    references: list[Mapping[str, Any]] = []
    for dimension in report["dimensions"]:
        references.extend(dimension["evidence"])
    for finding in report["findings"]:
        references.extend(finding["evidence"])
    for check in report["completeness"]["checks"]:
        references.extend(check["evidence"])
    references.extend(report["completeness"]["evidence"])
    return references


def _verify_bound_json_input(
    path: Path,
    *,
    label: str,
    expected_source_digest: str,
    expected_content_digest: str | None = None,
) -> None:
    data = _stable_read(path, label=label)
    if digest_bytes(data) != expected_source_digest:
        raise AdversarialReviewError(f"{label} bytes do not match the review subject")
    if expected_content_digest is None:
        return
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdversarialReviewError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or digest_obj(document) != expected_content_digest:
        raise AdversarialReviewError(f"{label} content does not match the review subject")


def _expected_verdict(report: Mapping[str, Any]) -> str:
    states = [str(item["state"]) for item in report["dimensions"]]
    states.extend(str(item["state"]) for item in report["completeness"]["checks"])
    states.append(str(report["completeness"]["state"]))
    if "STALE" in states:
        return "STALE"
    if any(state != "COMPLETED" for state in states):
        return "INCOMPLETE"
    refutations = [str(item["refutation"]["state"]) for item in report["findings"]]
    if "DISPUTED" in refutations:
        return "DISPUTED"
    if any(
        item["refutation"]["state"] == "SURVIVES" and item["severity"] == "blocking"
        for item in report["findings"]
    ):
        return "BLOCK"
    if "SURVIVES" in refutations:
        return "CHANGES_REQUESTED"
    return "CLEAN_QUALIFIED"


def _expected_completeness_state(states: Sequence[str]) -> str:
    """Collapse required check outcomes without disguising the failure class."""

    for state in ("STALE", "TIMEOUT", "PARSE_FAILED", "CAPABILITY_FAILED", "SKIPPED"):
        if state in states:
            return state
    return "COMPLETED"


def verify_validator_adversarial_review(
    report: Mapping[str, Any],
    *,
    subject: Mapping[str, Any],
    reviewer_identity: str,
    acceptance_observations: Mapping[str, Any],
    implementation_root: str | Path,
    tests_root: str | Path,
    build_input_path: str | Path,
    pattern_catalog_path: str | Path,
    build_plan_path: str | Path,
    acceptance_catalog_path: str | Path,
    acceptance_observations_path: str | Path,
) -> VerifiedAdversarialReview:
    """Re-derive coverage, finding identity, cited bytes, and the typed verdict."""

    try:
        validate_document("validator-review-subject", subject)
        validate_document("validator-adversarial-review", report)
    except DocumentValidationError as exc:
        raise AdversarialReviewError(str(exc)) from exc
    expected_protocol = {
        "protocol_id": REVIEW_PROTOCOL_ID,
        "protocol_digest": REVIEW_PROTOCOL_DIGEST,
        "report_schema_version": "factory-validator-adversarial-review/1",
        "required_dimensions": list(REQUIRED_REVIEW_DIMENSIONS),
        "required_completeness_checks": list(REQUIRED_COMPLETENESS_CHECKS),
    }
    if subject["protocol"] != expected_protocol:
        raise AdversarialReviewError("Validator review subject has the wrong protocol contract")
    if report["schema_version"] != subject["protocol"]["report_schema_version"]:
        raise AdversarialReviewError("Validator review report schema does not match its subject")
    subject_digest = digest_obj(dict(subject))
    if report["subject_digest"] != subject_digest:
        raise AdversarialReviewError("Validator review belongs to another immutable subject")
    if subject["reviewer_identity"] != reviewer_identity or report["reviewer_identity"] != (
        reviewer_identity
    ):
        raise AdversarialReviewError("Validator review has the wrong reviewer identity")
    if report["acceptance_observations_digest"] != digest_obj(dict(acceptance_observations)):
        raise AdversarialReviewError("Validator review cites different test observations")
    artifacts = subject["artifacts"]
    _verify_bound_json_input(
        Path(build_input_path),
        label="review build input",
        expected_source_digest=str(artifacts["build-input"]),
    )
    _verify_bound_json_input(
        Path(pattern_catalog_path),
        label="review pattern catalog",
        expected_source_digest=str(artifacts["pattern-catalog-source"]),
        expected_content_digest=str(artifacts["pattern-catalog"]),
    )
    _verify_bound_json_input(
        Path(build_plan_path),
        label="review build plan",
        expected_source_digest=str(artifacts["build-plan-source"]),
        expected_content_digest=str(artifacts["build-plan"]),
    )
    _verify_bound_json_input(
        Path(acceptance_catalog_path),
        label="review acceptance-obligation catalog",
        expected_source_digest=str(artifacts["acceptance-obligation-catalog-source"]),
        expected_content_digest=str(artifacts["acceptance-obligation-catalog"]),
    )
    try:
        if tree_digest(implementation_root) != artifacts["candidate"]:
            raise AdversarialReviewError(
                "review implementation tree does not match the review subject"
            )
        if tree_digest(tests_root) != artifacts["acceptance-tests"]:
            raise AdversarialReviewError("review test tree does not match the review subject")
    except SnapshotError as exc:
        raise AdversarialReviewError(f"review subject tree cannot be re-derived: {exc}") from exc
    dimensions = [str(item["dimension_id"]) for item in report["dimensions"]]
    if dimensions != list(REQUIRED_REVIEW_DIMENSIONS):
        raise AdversarialReviewError(
            "Validator review dimensions must match the exact code-owned order and membership"
        )
    for dimension in report["dimensions"]:
        if dimension["state"] == "COMPLETED" and not dimension["evidence"]:
            raise AdversarialReviewError(
                f"completed review dimension has no evidence: {dimension['dimension_id']}"
            )
    checks = [str(item["check_id"]) for item in report["completeness"]["checks"]]
    if checks != list(REQUIRED_COMPLETENESS_CHECKS):
        raise AdversarialReviewError(
            "Validator clean-claim checks must match the exact code-owned order and membership"
        )
    for check in report["completeness"]["checks"]:
        if check["state"] == "COMPLETED" and not check["evidence"]:
            raise AdversarialReviewError(
                f"completed clean-claim check has no evidence: {check['check_id']}"
            )
    check_states = [str(item["state"]) for item in report["completeness"]["checks"]]
    expected_completeness_state = _expected_completeness_state(check_states)
    if report["completeness"]["state"] != expected_completeness_state:
        raise AdversarialReviewError(
            "Validator clean-claim state does not re-derive from its required checks"
        )
    if report["completeness"]["state"] == "COMPLETED" and not (
        report["completeness"]["evidence"]
    ):
        raise AdversarialReviewError("completed clean-claim challenge has no evidence")
    finding_ids: list[str] = []
    for finding in report["findings"]:
        identity_body = {
            "dimension_id": finding["dimension_id"],
            "severity": finding["severity"],
            "statement": finding["statement"],
            "consequence": finding["consequence"],
            "evidence": list(finding["evidence"]),
        }
        expected_id = digest_obj(identity_body)
        if finding["finding_id"] != expected_id:
            raise AdversarialReviewError("Validator review finding identity does not re-derive")
        finding_ids.append(expected_id)
    if len(finding_ids) != len(set(finding_ids)):
        raise AdversarialReviewError("Validator review repeats a finding identity")
    evidence = _all_evidence(report)
    required_sources = {
        "implementation",
        "acceptance-tests",
        "build-input",
        "pattern-catalog",
        "build-plan",
        "acceptance-obligation-catalog",
        "acceptance-observations",
    }
    cited_sources = {str(reference["source"]) for reference in evidence}
    if cited_sources != required_sources:
        missing = ", ".join(sorted(required_sources - cited_sources))
        unexpected = ", ".join(sorted(cited_sources - required_sources))
        detail = f"missing={missing or 'none'}; unexpected={unexpected or 'none'}"
        raise AdversarialReviewError(
            f"Validator review did not cite the complete bound evidence set: {detail}"
        )
    for reference in evidence:
        _verify_evidence_reference(
            reference,
            implementation_root=Path(implementation_root),
            tests_root=Path(tests_root),
            build_input_path=Path(build_input_path),
            pattern_catalog_path=Path(pattern_catalog_path),
            build_plan_path=Path(build_plan_path),
            acceptance_catalog_path=Path(acceptance_catalog_path),
            acceptance_observations_path=Path(acceptance_observations_path),
        )
    expected_verdict = _expected_verdict(report)
    if report["verdict"] != expected_verdict:
        raise AdversarialReviewError(
            f"Validator review verdict must re-derive as {expected_verdict}"
        )
    return VerifiedAdversarialReview(
        subject=dict(subject),
        report=dict(report),
        subject_digest=subject_digest,
        report_digest=digest_obj(dict(report)),
    )


def _existing_is_identical(path: Path, content: bytes) -> bool:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AdversarialReviewError("retained Validator review is not regular")
        chunks: list[bytes] = []
        remaining = len(content) + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if b"".join(chunks) != content:
            return False
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        installed = os.lstat(path)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or stat.S_ISLNK(installed.st_mode)
            or (installed.st_dev, installed.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise AdversarialReviewError("retained Validator review changed during fsync")
        return True
    finally:
        os.close(descriptor)


def _write_once_or_identical(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".validator-review-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            if not _existing_is_identical(path, content):
                raise AdversarialReviewError(
                    "retained Validator review address contains different bytes"
                ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def retain_validator_adversarial_review(
    runs_root: str | Path,
    run_id: str,
    verified: VerifiedAdversarialReview,
) -> Mapping[str, str]:
    """Durably retain the exact subject and report before their ledger admission."""

    root = (
        Path(runs_root)
        / run_id
        / "evidence"
        / "validator-adversarial-reviews"
        / verified.subject_digest.removeprefix("sha256:")
    )
    _write_once_or_identical(root / "subject.json", canonical_document_bytes(verified.subject))
    _write_once_or_identical(
        root / f"{verified.report_digest.removeprefix('sha256:')}.json",
        canonical_document_bytes(verified.report),
    )
    try:
        fsync_directory_chain(root, through=Path(runs_root) / run_id)
    except DurabilityError as exc:
        raise AdversarialReviewError(str(exc)) from exc
    return {
        REVIEW_SUBJECT_ARTIFACT_KEY: verified.subject_digest,
        REVIEW_REPORT_ARTIFACT_KEY: verified.report_digest,
    }
