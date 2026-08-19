"""Ratified product acceptance obligations and point-for-point effect receipts.

The code-owned transition catalog in :mod:`factory_runtime.transition_obligations` protects the
Factory's own mechanics.  This module protects the target product's meaning.  A target catalog
does not become authority because an agent generated it: a distinct enrolled human and Validator
ratify the exact catalog, every obligation resolves to the current three phase artifacts, and the
runtime selects a trigger by an exact state pair.  Unknown or ambiguous selectors deny.

Validator observations are not accepted as proof merely because they say ``passed``.  The host
re-derives their subject, exact test membership, command/configuration/environment bindings and
every cited evidence digest from independently trusted values before retaining a report.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_core.provenance import (
    CLAIM_TEST_ASSERTION,
    IntentBackreference,
    PhaseArtifact,
    ProvenanceBundle,
    ProvenanceClaim,
)
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    VerifiedReceipt,
    verify_receipt,
)
from factory_runtime.durability import DurabilityError, fsync_directory_chain
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunState, RunStore
from factory_runtime.tessera import TesseraCli

CATALOG_ARTIFACT_KEY = "acceptance-obligation-catalog"
CATALOG_HUMAN_RECEIPT_KEY = f"{CATALOG_ARTIFACT_KEY}:human-receipt"
CATALOG_VALIDATOR_RECEIPT_KEY = f"{CATALOG_ARTIFACT_KEY}:validator-receipt"
REPORT_ARTIFACT_KEY = "acceptance-obligation-report"
RATIFY_ACTION = "ratify-acceptance-obligation-catalog"
REQUIRED_TRIGGER = ("validating", "preview")
TRUSTED_EVIDENCE_IDS = frozenset(
    {
        "candidate",
        "acceptance-tests",
        "coder-output-snapshot",
        "tester-output-snapshot",
    }
)
_VALIDATOR_ENVIRONMENT_CONTRACT = {
    "schema_version": "factory-validator-environment/1",
    "ambient_environment": "closed",
    "network": "denied",
    "read_scope": [
        "build-input",
        "build-plan",
        "pattern-catalog",
        "acceptance-obligation-catalog",
        "coder-output-snapshot",
        "tester-output-snapshot",
        "trusted-runner-paths",
    ],
    "write_scope": ["validator-work", "validator-output"],
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class AcceptanceObligationError(ValueError):
    """A target acceptance obligation could not be authorized or proved."""


def validator_execution_digests(command: Sequence[str]) -> tuple[str, str, str]:
    """Return the exact command, runner configuration, and closed-environment addresses.

    These values are ratified in the catalog before authoring.  The caller cannot describe a
    friendlier environment than the runtime actually supplies: the environment contract is
    code-owned, while the command bytes remain an explicit human decision.
    """

    argv = [str(part) for part in command]
    if not argv or any(not part for part in argv):
        raise AcceptanceObligationError("Validator command cannot be empty")
    command_digest = digest_obj({"argv": argv})
    configuration_digest = digest_obj(
        {
            "schema_version": "factory-validator-configuration/1",
            "runner": "isolated-build-loop/1",
            "command_digest": command_digest,
        }
    )
    return (
        command_digest,
        configuration_digest,
        digest_obj(_VALIDATOR_ENVIRONMENT_CONTRACT),
    )


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _read_regular_bytes(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise AcceptanceObligationError(f"{label} is unreadable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise AcceptanceObligationError(f"{label} is not regular")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    raw_bytes = _read_regular_bytes(path, label=label)
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise AcceptanceObligationError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise AcceptanceObligationError(f"{label} must be a JSON object")
    return dict(raw)


def _read_canonical_object(path: str | Path, *, label: str) -> dict[str, Any]:
    raw_bytes = _read_regular_bytes(path, label=label)
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise AcceptanceObligationError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise AcceptanceObligationError(f"{label} must be a JSON object")
    document = dict(raw)
    if raw_bytes != _canonical_bytes(document):
        raise AcceptanceObligationError(f"{label} is not in canonical retained form")
    return document


def _sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise AcceptanceObligationError(f"acceptance evidence path is not a directory: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_evidence_directories(path: Path) -> None:
    _sync_directory(path.parent)
    _sync_directory(path.parent.parent)


def _existing_file_is_identical(path: Path, content: bytes) -> bool:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise AcceptanceObligationError(
            f"acceptance-obligation evidence became unreadable: {exc}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AcceptanceObligationError(
                "acceptance-obligation evidence destination is not regular"
            )
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
        if not stable:
            raise AcceptanceObligationError(
                "acceptance-obligation evidence changed during comparison"
            )
        if b"".join(chunks) != content:
            return False
        os.fsync(descriptor)
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise AcceptanceObligationError("acceptance-obligation evidence changed during fsync")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _sync_evidence_directories(path)
    return True


def _write_once_or_identical(path: Path, content: bytes) -> None:
    """Publish one complete immutable file with an atomic no-replace hard link."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".acceptance-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            if not _existing_file_is_identical(path, content):
                raise AcceptanceObligationError(
                    "acceptance-obligation evidence address contains different bytes"
                ) from exc
            return
        _sync_evidence_directories(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class AcceptanceObligationCatalog:
    """Closed, exact-selector catalog whose content address is the ratified subject."""

    document: Mapping[str, Any]

    @property
    def content_digest(self) -> str:
        return digest_obj(dict(self.document))

    def select(self, source: str, destination: str) -> Mapping[str, Any]:
        matches = [
            trigger
            for trigger in self.document["triggers"]
            if trigger["from_state"] == source and trigger["to_state"] == destination
        ]
        if len(matches) != 1:
            qualifier = "unknown" if not matches else "ambiguous"
            raise AcceptanceObligationError(
                f"{qualifier} acceptance-obligation selector: {source} -> {destination}"
            )
        return matches[0]

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> AcceptanceObligationCatalog:
        try:
            validate_document("acceptance-obligation-catalog", document)
        except DocumentValidationError as exc:
            raise AcceptanceObligationError(str(exc)) from exc
        triggers = list(document["triggers"])
        trigger_ids = [str(trigger["trigger_id"]) for trigger in triggers]
        pairs = [(str(trigger["from_state"]), str(trigger["to_state"])) for trigger in triggers]
        if len(trigger_ids) != len(set(trigger_ids)):
            raise AcceptanceObligationError("acceptance-obligation trigger ids must be unique")
        if len(pairs) != len(set(pairs)):
            raise AcceptanceObligationError("acceptance-obligation state selectors must be unique")
        if REQUIRED_TRIGGER not in pairs:
            raise AcceptanceObligationError(
                "acceptance-obligation catalog must define validating -> preview"
            )
        obligation_ids: list[str] = []
        for trigger in triggers:
            local_ids = [str(item["obligation_id"]) for item in trigger["obligations"]]
            if len(local_ids) != len(set(local_ids)):
                raise AcceptanceObligationError(
                    f"trigger {trigger['trigger_id']} contains duplicate obligation ids"
                )
            obligation_ids.extend(local_ids)
            for obligation in trigger["obligations"]:
                unknown_evidence = sorted(
                    set(obligation["required_evidence_ids"]) - TRUSTED_EVIDENCE_IDS
                )
                if unknown_evidence:
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} requests unsupported "
                        f"evidence ids: {', '.join(unknown_evidence)}"
                    )
                references = [
                    json.dumps(reference, sort_keys=True, separators=(",", ":"))
                    for reference in obligation["intent_backreferences"]
                ]
                if len(references) != len(set(references)):
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} repeats an intent backreference"
                    )
                test_pairs = [
                    (str(item["test_id"]), str(item["assertion_digest"]))
                    for item in obligation["test_assertions"]
                ]
                if len(test_pairs) != len(set(test_pairs)):
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} repeats a test assertion"
                    )
                if obligation["verifier_id"] == "validator-test-execution-v1" and not test_pairs:
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} test verifier has no exact tests"
                    )
                if obligation["verifier_id"] != "validator-test-execution-v1" and test_pairs:
                    raise AcceptanceObligationError(
                        f"obligation {obligation['obligation_id']} assigns tests to a "
                        "non-test verifier"
                    )
        if len(obligation_ids) != len(set(obligation_ids)):
            raise AcceptanceObligationError(
                "acceptance-obligation ids must be unique across the catalog"
            )
        return cls(dict(document))


@dataclass(frozen=True)
class StoredAcceptanceCatalog:
    catalog: AcceptanceObligationCatalog
    human_receipt: VerifiedReceipt
    validator_receipt: VerifiedReceipt
    directory: Path

    @property
    def artifact_digests(self) -> Mapping[str, str]:
        return {
            CATALOG_ARTIFACT_KEY: self.catalog.content_digest,
            CATALOG_HUMAN_RECEIPT_KEY: self.human_receipt.envelope.envelope_digest,
            CATALOG_VALIDATOR_RECEIPT_KEY: self.validator_receipt.envelope.envelope_digest,
        }


def _phase_artifacts(runs_root: Path, run_id: str) -> tuple[PhaseArtifact, ...]:
    projection = RunStore(runs_root).load(run_id)
    artifacts: list[PhaseArtifact] = []
    for phase, expected_digest in projection.phase_artifact_digests.items():
        path = (
            runs_root
            / run_id
            / "evidence"
            / phase
            / expected_digest.removeprefix("sha256:")
            / "artifact.json"
        )
        document = _read_object(path, label=f"retained {phase} artifact")
        try:
            validate_document("phase-artifact", document)
        except DocumentValidationError as exc:
            raise AcceptanceObligationError(str(exc)) from exc
        artifact = PhaseArtifact.from_dict(document)
        if artifact.phase != phase or artifact.content_digest != expected_digest:
            raise AcceptanceObligationError(
                f"retained {phase} artifact differs from the run ledger"
            )
        artifacts.append(artifact)
    return tuple(artifacts)


def _verify_catalog_provenance(
    catalog: AcceptanceObligationCatalog,
    artifacts: Sequence[PhaseArtifact],
) -> None:
    trusted = {artifact.artifact_id: artifact.content_digest for artifact in artifacts}
    claims: list[ProvenanceClaim] = []
    for trigger in catalog.document["triggers"]:
        for obligation in trigger["obligations"]:
            for index, reference in enumerate(obligation["intent_backreferences"], start=1):
                claims.append(
                    ProvenanceClaim(
                        claim_id=f"{obligation['obligation_id']}.{index}",
                        kind=CLAIM_TEST_ASSERTION,
                        backreference=IntentBackreference.from_dict(reference),
                    )
                )
    report = ProvenanceBundle(
        artifacts=tuple(artifacts),
        claims=tuple(claims),
        trusted_artifact_digests=trusted,
    ).verify()
    if not report.satisfied:
        raise AcceptanceObligationError(
            "acceptance-obligation intent provenance is invalid: " + ", ".join(report.issues)
        )


def verify_and_retain_acceptance_catalog(
    runs_root: str | Path,
    run_id: str,
    *,
    catalog_path: str | Path,
    human_receipt_path: str | Path,
    validator_receipt_path: str | Path,
    policy: AuthorityPolicy,
    tessera: TesseraCli,
    clock: Callable[[], int] | None = None,
) -> StoredAcceptanceCatalog:
    """Verify independent ratification and retain exact catalog/receipt bytes before build."""

    root = Path(runs_root)
    projection = RunStore(root).load(run_id)
    if projection.state != RunState.OPERATIONAL_MATURITY_RATIFIED:
        raise AcceptanceObligationError(
            "a new acceptance-obligation catalog requires operational-maturity ratification"
        )
    document = _read_object(catalog_path, label="acceptance-obligation catalog")
    catalog = AcceptanceObligationCatalog.from_dict(document)
    expected = {
        "run_id": run_id,
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
    }
    for field, value in expected.items():
        if catalog.document[field] != value:
            raise AcceptanceObligationError(f"acceptance-obligation catalog has wrong {field}")
    human_identity = str(catalog.document["human_ratifier"])
    validator_identity = str(catalog.document["validator_ratifier"])
    if human_identity == validator_identity:
        raise AcceptanceObligationError(
            "acceptance-obligation human and Validator ratifiers must be distinct"
        )
    _verify_catalog_provenance(catalog, _phase_artifacts(root, run_id))
    human_envelope_bytes = _read_regular_bytes(
        human_receipt_path,
        label="acceptance-obligation human receipt",
    )
    validator_envelope_bytes = _read_regular_bytes(
        validator_receipt_path,
        label="acceptance-obligation Validator receipt",
    )
    consumed = RunStore(root).consumed_authority_nonces(run_id)
    try:
        human_receipt = verify_receipt(
            human_receipt_path,
            policy=policy,
            expected_action=RATIFY_ACTION,
            expected_subject_digest=catalog.content_digest,
            expected_run_id=run_id,
            expected_signer_identity=human_identity,
            tessera=tessera,
            clock=clock,
            consumed_nonces=tuple(consumed),
        )
        human = policy.principal(human_identity)
        if human is None or human.kind != "human":
            raise AuthorityVerificationError(
                "acceptance-obligation human ratifier is not an enrolled human"
            )
        validator_receipt = verify_receipt(
            validator_receipt_path,
            policy=policy,
            expected_action=RATIFY_ACTION,
            expected_subject_digest=catalog.content_digest,
            expected_run_id=run_id,
            expected_signer_identity=validator_identity,
            tessera=tessera,
            clock=clock,
            consumed_nonces=tuple((*consumed, human_receipt.nonce)),
        )
        validator = policy.principal(validator_identity)
        if validator is None or validator.kind != "agent":
            raise AuthorityVerificationError(
                "acceptance-obligation Validator ratifier is not an enrolled agent"
            )
        if digest_bytes(human_envelope_bytes) != human_receipt.envelope.envelope_digest:
            raise AuthorityVerificationError(
                "acceptance-obligation human receipt changed while it was verified"
            )
        if digest_bytes(validator_envelope_bytes) != validator_receipt.envelope.envelope_digest:
            raise AuthorityVerificationError(
                "acceptance-obligation Validator receipt changed while it was verified"
            )
    except AuthorityVerificationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc

    directory = (
        root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / catalog.content_digest.removeprefix("sha256:")
    )
    _write_once_or_identical(directory / "catalog.json", _canonical_bytes(catalog.document))
    _write_once_or_identical(
        directory / "human-receipt.tessera.json",
        human_envelope_bytes,
    )
    _write_once_or_identical(
        directory / "validator-receipt.tessera.json",
        validator_envelope_bytes,
    )
    try:
        fsync_directory_chain(directory, through=root / run_id)
    except DurabilityError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    return StoredAcceptanceCatalog(catalog, human_receipt, validator_receipt, directory)


def load_retained_acceptance_catalog(
    runs_root: str | Path,
    run_id: str,
    *,
    expected_digest: str | None = None,
) -> AcceptanceObligationCatalog:
    root = Path(runs_root)
    projection = RunStore(root).load(run_id)
    digest = expected_digest or projection.acceptance_obligation_catalog_digest
    if not digest:
        raise AcceptanceObligationError("run has no ratified acceptance-obligation catalog")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise AcceptanceObligationError(
            "acceptance-obligation catalog digest is not a canonical content address"
        )
    path = (
        root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / digest.removeprefix("sha256:")
        / "catalog.json"
    )
    catalog = AcceptanceObligationCatalog.from_dict(
        _read_object(path, label="retained acceptance-obligation catalog")
    )
    if catalog.content_digest != digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog differs from its ledger address"
        )
    if catalog.document["target_state_digest"] != projection.target_state_digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog targets another subject"
        )
    if catalog.document["generation"] != projection.generation:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog targets another generation"
        )
    if catalog.document["phase_artifact_digests"] != dict(projection.phase_artifact_digests):
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog has stale phase versions"
        )
    _verify_catalog_provenance(catalog, _phase_artifacts(root, run_id))
    return catalog


def derive_acceptance_obligation_report(
    catalog: AcceptanceObligationCatalog,
    *,
    observations: Mapping[str, Any],
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    phase_artifact_digests: Mapping[str, str],
    candidate_digest: str,
    acceptance_tests_digest: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
    trusted_evidence_digests: Mapping[str, str],
) -> dict[str, Any]:
    """Re-derive one exact trigger report from Validator observations and trusted evidence."""

    try:
        validate_document("acceptance-obligation-observations", observations)
    except DocumentValidationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    trigger = catalog.select(source, destination)
    catalog_subject = {
        "run_id": run_id,
        "generation": generation,
        "target_state_digest": target_state_digest,
        "phase_artifact_digests": dict(phase_artifact_digests),
    }
    for field, expected in catalog_subject.items():
        if catalog.document[field] != expected:
            raise AcceptanceObligationError(
                f"acceptance-obligation catalog has stale or substituted {field}"
            )
    execution_contract = {
        "command_digest": command_digest,
        "configuration_digest": configuration_digest,
        "environment_digest": environment_digest,
    }
    for field, expected in execution_contract.items():
        if trigger[field] != expected:
            raise AcceptanceObligationError(
                f"acceptance-obligation trigger does not authorize the exact {field}"
            )
    exact = {
        "run_id": run_id,
        "generation": generation,
        "catalog_digest": catalog.content_digest,
        "trigger_id": trigger["trigger_id"],
        "candidate_digest": candidate_digest,
        "acceptance_tests_digest": acceptance_tests_digest,
        "command_digest": command_digest,
        "configuration_digest": configuration_digest,
        "environment_digest": environment_digest,
    }
    for field, expected in exact.items():
        if observations[field] != expected:
            raise AcceptanceObligationError(
                f"acceptance-obligation observations have wrong {field}"
            )
    if int(observations["finished_at"]) < int(observations["started_at"]):
        raise AcceptanceObligationError("acceptance-obligation observation time runs backwards")
    expected_obligations = list(trigger["obligations"])
    observed_results = list(observations["results"])
    expected_ids = [str(item["obligation_id"]) for item in expected_obligations]
    observed_ids = [str(item["obligation_id"]) for item in observed_results]
    if observed_ids != expected_ids:
        raise AcceptanceObligationError(
            "acceptance-obligation results must match the ratified order and exact membership"
        )

    report_results: list[dict[str, Any]] = []
    for obligation, result in zip(expected_obligations, observed_results, strict=True):
        obligation_id = str(obligation["obligation_id"])
        if result["verifier_id"] != obligation["verifier_id"]:
            raise AcceptanceObligationError(
                f"obligation {obligation_id} changed its code-owned verifier"
            )
        evidence = {str(key): str(value) for key, value in result["evidence_digests"].items()}
        required_evidence = list(obligation["required_evidence_ids"])
        if set(evidence) != set(required_evidence):
            raise AcceptanceObligationError(
                f"obligation {obligation_id} evidence membership differs from its ratified set"
            )
        for evidence_id, claimed_digest in evidence.items():
            if trusted_evidence_digests.get(evidence_id) != claimed_digest:
                raise AcceptanceObligationError(
                    f"obligation {obligation_id} cites untrusted evidence {evidence_id}"
                )
        expected_tests = [
            (str(item["test_id"]), str(item["assertion_digest"]))
            for item in obligation["test_assertions"]
        ]
        observed_tests = [
            (str(item["test_id"]), str(item["assertion_digest"])) for item in result["test_results"]
        ]
        if observed_tests != expected_tests:
            raise AcceptanceObligationError(
                f"obligation {obligation_id} did not execute the exact ratified test selection"
            )
        if obligation["verifier_id"] == "validator-test-execution-v1" and not observed_tests:
            raise AcceptanceObligationError(
                f"obligation {obligation_id} has a vacuous test execution"
            )
        for test_result in result["test_results"]:
            expected_output_digest = digest_obj(
                {
                    "test_id": test_result["test_id"],
                    "assertion_digest": test_result["assertion_digest"],
                    "exit_status": 0,
                    "candidate_digest": candidate_digest,
                    "acceptance_tests_digest": acceptance_tests_digest,
                    "command_digest": command_digest,
                }
            )
            if test_result["output_digest"] != expected_output_digest:
                raise AcceptanceObligationError(
                    f"obligation {obligation_id} test output receipt does not re-derive"
                )
        effect_body = {
            "obligation_id": obligation_id,
            "verifier_id": obligation["verifier_id"],
            "candidate_digest": candidate_digest,
            "acceptance_tests_digest": acceptance_tests_digest,
            "command_digest": command_digest,
            "configuration_digest": configuration_digest,
            "environment_digest": environment_digest,
            "started_at": observations["started_at"],
            "finished_at": observations["finished_at"],
            "evidence_digests": evidence,
            "test_results": list(result["test_results"]),
        }
        if result["effect_digest"] != digest_obj(effect_body):
            raise AcceptanceObligationError(
                f"obligation {obligation_id} effect digest does not re-derive"
            )
        report_results.append(
            {
                "obligation_id": obligation_id,
                "criterion": obligation["criterion"],
                "verifier_id": obligation["verifier_id"],
                "intent_backreferences": list(obligation["intent_backreferences"]),
                "required_evidence_ids": required_evidence,
                "test_assertions": list(obligation["test_assertions"]),
                "evidence_digests": evidence,
                "test_results": list(result["test_results"]),
                "effect_digest": result["effect_digest"],
                "passed": True,
            }
        )
    # ``satisfied`` means every ratified assertion has a matching deterministic observation with
    # exit status zero and re-derived evidence digests. Semantic adequacy comes from the human and
    # Validator-ratified obligation/test membership, not from this boolean by itself.
    document = {
        "schema_version": "factory-acceptance-obligation-report/1",
        "run_id": run_id,
        "generation": generation,
        "catalog_digest": catalog.content_digest,
        "trigger_id": trigger["trigger_id"],
        "from_state": source,
        "to_state": destination,
        "target_state_digest": target_state_digest,
        "resolved_commit": resolved_commit,
        "resolved_tree": resolved_tree,
        "phase_artifact_digests": dict(phase_artifact_digests),
        "candidate_digest": candidate_digest,
        "acceptance_tests_digest": acceptance_tests_digest,
        "observations": dict(observations),
        "observations_digest": digest_obj(dict(observations)),
        "command_digest": command_digest,
        "configuration_digest": configuration_digest,
        "environment_digest": environment_digest,
        "started_at": observations["started_at"],
        "finished_at": observations["finished_at"],
        "idempotency_key": digest_obj(
            {
                "catalog_digest": catalog.content_digest,
                "trigger_id": trigger["trigger_id"],
                "candidate_digest": candidate_digest,
                "acceptance_tests_digest": acceptance_tests_digest,
                "observations_digest": digest_obj(dict(observations)),
            }
        ),
        "results": report_results,
        "satisfied": True,
    }
    try:
        validate_document("acceptance-obligation-report", document)
    except DocumentValidationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    return document


def verify_acceptance_obligation_report(
    catalog: AcceptanceObligationCatalog,
    report: Mapping[str, Any],
    *,
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    phase_artifact_digests: Mapping[str, str],
    candidate_digest: str,
    acceptance_tests_digest: str,
    command_digest: str,
    configuration_digest: str,
    environment_digest: str,
    trusted_evidence_digests: Mapping[str, str],
) -> None:
    """Re-derive a retained report from its raw observations and exact runtime subject."""

    try:
        validate_document("acceptance-obligation-report", report)
    except DocumentValidationError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    observations = report.get("observations")
    if not isinstance(observations, Mapping):
        raise AcceptanceObligationError("acceptance-obligation report has no observations")
    expected = derive_acceptance_obligation_report(
        catalog,
        observations=observations,
        run_id=run_id,
        generation=generation,
        source=source,
        destination=destination,
        target_state_digest=target_state_digest,
        resolved_commit=resolved_commit,
        resolved_tree=resolved_tree,
        phase_artifact_digests=phase_artifact_digests,
        candidate_digest=candidate_digest,
        acceptance_tests_digest=acceptance_tests_digest,
        command_digest=command_digest,
        configuration_digest=configuration_digest,
        environment_digest=environment_digest,
        trusted_evidence_digests=trusted_evidence_digests,
    )
    if digest_obj(dict(report)) != digest_obj(expected):
        raise AcceptanceObligationError(
            "acceptance-obligation report differs from fresh derivation"
        )


def retain_acceptance_obligation_report(
    runs_root: str | Path,
    run_id: str,
    report: Mapping[str, Any],
) -> str:
    digest = digest_obj(dict(report))
    root = (
        Path(runs_root)
        / run_id
        / "evidence"
        / "acceptance-obligation-reports"
        / str(report["catalog_digest"]).removeprefix("sha256:")
    )
    _write_once_or_identical(
        root / f"{digest.removeprefix('sha256:')}.json", _canonical_bytes(report)
    )
    try:
        fsync_directory_chain(root, through=Path(runs_root) / run_id)
    except DurabilityError as exc:
        raise AcceptanceObligationError(str(exc)) from exc
    return digest


def verify_retained_acceptance_obligation_report(
    run_dir: str | Path,
    *,
    catalog_digest: str,
    report_digest: str,
    run_id: str,
    generation: int,
    source: str,
    destination: str,
    target_state_digest: str,
    resolved_commit: str,
    resolved_tree: str,
    phase_artifact_digests: Mapping[str, str],
    candidate_digest: str,
    acceptance_tests_digest: str,
    trusted_evidence_digests: Mapping[str, str],
) -> Mapping[str, Any]:
    """Reopen and re-derive the ratified catalog and report behind a ledger transition."""

    root = Path(run_dir)
    catalog_path = (
        root
        / "evidence"
        / "acceptance-obligation-catalogs"
        / catalog_digest.removeprefix("sha256:")
        / "catalog.json"
    )
    catalog_document = _read_canonical_object(
        catalog_path, label="retained acceptance-obligation catalog"
    )
    catalog = AcceptanceObligationCatalog.from_dict(catalog_document)
    if catalog.content_digest != catalog_digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation catalog differs from its content address"
        )
    report_path = (
        root
        / "evidence"
        / "acceptance-obligation-reports"
        / catalog_digest.removeprefix("sha256:")
        / f"{report_digest.removeprefix('sha256:')}.json"
    )
    report = _read_canonical_object(report_path, label="retained acceptance-obligation report")
    if digest_obj(report) != report_digest:
        raise AcceptanceObligationError(
            "retained acceptance-obligation report differs from its content address"
        )
    # The execution contract comes from the independently ratified catalog.  A retained report
    # is evidence, never an authority source for the command/configuration/environment it claims.
    trigger = catalog.select(source, destination)
    verify_acceptance_obligation_report(
        catalog,
        report,
        run_id=run_id,
        generation=generation,
        source=source,
        destination=destination,
        target_state_digest=target_state_digest,
        resolved_commit=resolved_commit,
        resolved_tree=resolved_tree,
        phase_artifact_digests=phase_artifact_digests,
        candidate_digest=candidate_digest,
        acceptance_tests_digest=acceptance_tests_digest,
        command_digest=str(trigger["command_digest"]),
        configuration_digest=str(trigger["configuration_digest"]),
        environment_digest=str(trigger["environment_digest"]),
        trusted_evidence_digests=trusted_evidence_digests,
    )
    return report


__all__ = [
    "AcceptanceObligationCatalog",
    "AcceptanceObligationError",
    "CATALOG_ARTIFACT_KEY",
    "CATALOG_HUMAN_RECEIPT_KEY",
    "CATALOG_VALIDATOR_RECEIPT_KEY",
    "RATIFY_ACTION",
    "REPORT_ARTIFACT_KEY",
    "StoredAcceptanceCatalog",
    "derive_acceptance_obligation_report",
    "load_retained_acceptance_catalog",
    "retain_acceptance_obligation_report",
    "validator_execution_digests",
    "verify_acceptance_obligation_report",
    "verify_retained_acceptance_obligation_report",
    "verify_and_retain_acceptance_catalog",
]
