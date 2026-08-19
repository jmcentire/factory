"""Dual-signed authority for changing formerly correct test expectations.

A phase artifact may deliberately supersede an old behavior, but that fact alone does not let an
agent edit an existing guardrail.  This boundary verifies a second, exact ruling over the run,
generation, target state, current phase versions, old and new behavior, and the frozen assertions
whose expectations may change.  An enrolled human and a distinct enrolled Validator must sign the
same content address before the authorization can enter the lifecycle ledger.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes
from factory_core.provenance import REQUIRED_PHASES, IntentBackreference, PhaseArtifact
from factory_core.test_disposition import (
    TEST_CHANGE_RULING,
    TestChangeAuthorization,
)
from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    VerifiedReceipt,
    verify_receipt,
)
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import (
    TEST_CHANGE_AUTHORIZATION_KEY,
    RunState,
    RunStore,
)
from factory_runtime.tessera import TesseraCli

HUMAN_RECEIPT_KEY = f"{TEST_CHANGE_AUTHORIZATION_KEY}:human-receipt"
VALIDATOR_RECEIPT_KEY = f"{TEST_CHANGE_AUTHORIZATION_KEY}:validator-receipt"
RATIFY_ACTION = "ratify-test-change-authorization"
_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
_RESERVATION_FILE = "nonce-reservations.json"
_ADDRESS = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class TestChangeAuthorityError(ValueError):
    """The proposed test expectation change is not exactly and independently authorized."""


@dataclass(frozen=True)
class StoredTestChangeAuthorization:
    authorization: TestChangeAuthorization
    human_receipt: VerifiedReceipt
    validator_receipt: VerifiedReceipt
    directory: Path

    @property
    def artifact_digests(self) -> Mapping[str, str]:
        return {
            TEST_CHANGE_AUTHORIZATION_KEY: self.authorization.content_digest,
            HUMAN_RECEIPT_KEY: self.human_receipt.envelope.envelope_digest,
            VALIDATOR_RECEIPT_KEY: self.validator_receipt.envelope.envelope_digest,
        }

    @property
    def authority_nonces(self) -> tuple[str, str]:
        return (self.human_receipt.nonce, self.validator_receipt.nonce)


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _read_regular_bytes(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise TestChangeAuthorityError(f"{label} is unreadable: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TestChangeAuthorityError(f"{label} is not regular")
        if metadata.st_size > _MAX_DOCUMENT_BYTES:
            raise TestChangeAuthorityError(f"{label} exceeds the retained evidence size limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(_MAX_DOCUMENT_BYTES + 1)
        if len(content) > _MAX_DOCUMENT_BYTES:
            raise TestChangeAuthorityError(f"{label} exceeds the retained evidence size limit")
        return content
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_object(path: str | Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    content = _read_regular_bytes(path, label=label)
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise TestChangeAuthorityError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise TestChangeAuthorityError(f"{label} must be a JSON object")
    return dict(raw), content


def _write_new_file(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise TestChangeAuthorityError(f"could not create retained authority file: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TestChangeAuthorityError(
                "test-change authority destination is not a regular file"
            )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise TestChangeAuthorityError(f"authority path is not a directory: {path}")
        os.fsync(descriptor)
        installed = os.lstat(path)
        if stat.S_ISLNK(installed.st_mode) or (
            installed.st_dev,
            installed.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise TestChangeAuthorityError(f"authority directory changed during fsync: {path}")
    finally:
        os.close(descriptor)


def _require_private_directory(path: Path, *, label: str) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise TestChangeAuthorityError(f"{label} must be a real directory")
    os.chmod(path, 0o700)


def _sync_identical_file(path: Path, content: bytes) -> bool:
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
            raise TestChangeAuthorityError(
                "retained test-change authority file changed during fsync"
            )
    except OSError as exc:
        raise TestChangeAuthorityError(
            f"retained test-change authority became unreadable: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return True


def _sync_identical_bundle(directory: Path, files: Mapping[str, bytes]) -> bool:
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise TestChangeAuthorityError("retained test-change authority address is not a directory")
    actual_names = {entry.name for entry in directory.iterdir()}
    if actual_names != set(files):
        return False
    if not all(_sync_identical_file(directory / name, content) for name, content in files.items()):
        return False
    current = directory.lstat()
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise TestChangeAuthorityError(
            "retained test-change authority directory changed during comparison"
        )
    if {entry.name for entry in directory.iterdir()} != set(files):
        raise TestChangeAuthorityError(
            "retained test-change authority membership changed during comparison"
        )
    _sync_directory(directory)
    _sync_directory(directory.parent)
    return True


def _remove_pending_bundle(directory: Path, names: Sequence[str]) -> None:
    """Remove only the exact temporary bundle created by this invocation."""

    for name in names:
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
    try:
        directory.rmdir()
    except FileNotFoundError:
        pass


def _retain_bundle_atomically(
    *,
    run_dir: Path,
    final_directory: Path,
    files: Mapping[str, bytes],
) -> None:
    """Publish a complete authority bundle with one same-filesystem directory rename."""

    parent = final_directory.parent
    _require_private_directory(parent, label="test-change authority parent")
    if _sync_identical_bundle(final_directory, files):
        return
    if final_directory.exists() or final_directory.is_symlink():
        raise TestChangeAuthorityError(
            "test-change authority address already contains different bytes"
        )

    staging_parent = run_dir / ".staging" / "test-change-authority"
    _require_private_directory(staging_parent, label="test-change authority staging directory")
    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=staging_parent))
    os.chmod(pending, 0o700)
    names = tuple(files)
    installed = False
    try:
        for name, content in files.items():
            if Path(name).name != name:
                raise TestChangeAuthorityError("authority bundle filename is not canonical")
            _write_new_file(pending / name, content)
        _sync_directory(pending)
        try:
            os.rename(pending, final_directory)
            installed = True
        except OSError as exc:
            if _sync_identical_bundle(final_directory, files):
                return
            raise TestChangeAuthorityError(
                "could not atomically install test-change authority bundle"
            ) from exc
        _sync_directory(parent)
    finally:
        if not installed:
            _remove_pending_bundle(pending, names)


@contextmanager
def _authority_guard(run_dir: Path) -> Iterator[None]:
    """Serialize verification, nonce reservation, and retention for one run."""

    guard_path = run_dir / "test-change-authority.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(guard_path, flags, 0o600)
    except OSError as exc:
        raise TestChangeAuthorityError(f"test-change authority lock is unavailable: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TestChangeAuthorityError("test-change authority lock is not regular")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _reserved_nonces(parent: Path, *, exclude_address: str) -> frozenset[str]:
    """Read nonce reservations only from complete, atomically published bundles."""

    if not parent.exists():
        return frozenset()
    metadata = parent.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise TestChangeAuthorityError("test-change authority store is not a real directory")
    reservations: set[str] = set()
    for directory in parent.iterdir():
        if directory.name == exclude_address or directory.name.startswith("."):
            continue
        if (
            not _ADDRESS.fullmatch(directory.name)
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            raise TestChangeAuthorityError("test-change authority store contains an invalid entry")
        document, _ = _read_object(
            directory / _RESERVATION_FILE,
            label="retained test-change nonce reservation",
        )
        raw_nonces = document.get("nonces")
        raw_receipt_digests = document.get("receipt_digests")
        if (
            set(document)
            != {
                "schema_version",
                "authorization_digest",
                "receipt_digests",
                "nonces",
            }
            or document.get("schema_version") != "factory-authority-nonce-reservation/1"
        ):
            raise TestChangeAuthorityError("retained test-change nonce reservation is malformed")
        if document.get("authorization_digest") != f"sha256:{directory.name}":
            raise TestChangeAuthorityError("retained test-change nonce reservation is misaddressed")
        if (
            not isinstance(raw_receipt_digests, list)
            or len(raw_receipt_digests) != 2
            or any(not _DIGEST.fullmatch(str(value)) for value in raw_receipt_digests)
            or len({str(value) for value in raw_receipt_digests}) != 2
        ):
            raise TestChangeAuthorityError("retained test-change nonce reservation is malformed")
        if not isinstance(raw_nonces, list) or len(raw_nonces) != 2:
            raise TestChangeAuthorityError("retained test-change nonce reservation is malformed")
        normalized = [str(nonce) for nonce in raw_nonces]
        if any(not nonce.strip() for nonce in normalized) or len(set(normalized)) != 2:
            raise TestChangeAuthorityError("retained test-change nonce reservation is malformed")
        reservations.update(normalized)
    return frozenset(reservations)


def _retained_phase_artifacts(
    root: Path,
    run_id: str,
    phase_digests: Mapping[str, str],
) -> tuple[PhaseArtifact, ...]:
    artifacts: list[PhaseArtifact] = []
    for phase in REQUIRED_PHASES:
        expected_digest = str(phase_digests.get(phase, ""))
        path = (
            root
            / run_id
            / "evidence"
            / phase
            / expected_digest.removeprefix("sha256:")
            / "artifact.json"
        )
        document, _ = _read_object(path, label=f"retained {phase} artifact")
        try:
            validate_document("phase-artifact", document)
        except DocumentValidationError as exc:
            raise TestChangeAuthorityError(str(exc)) from exc
        artifact = PhaseArtifact.from_dict(document)
        if artifact.phase != phase or artifact.content_digest != expected_digest:
            raise TestChangeAuthorityError(f"retained {phase} artifact differs from the run ledger")
        artifacts.append(artifact)
    return tuple(artifacts)


def _verify_replacement(
    authorization: TestChangeAuthorization,
    artifacts: Sequence[PhaseArtifact],
) -> None:
    old = authorization.old_behavior
    new = authorization.new_behavior
    if old is None or new is None:
        raise TestChangeAuthorityError(
            "test-change authorization must name exact old and new behavior"
        )
    matches: list[tuple[PhaseArtifact, str, tuple[IntentBackreference, ...]]] = []
    for artifact in artifacts:
        for item in artifact.items:
            if artifact.backreference(item) == new:
                matches.append((artifact, item.canonical_statement, item.supersedes))
    if len(matches) != 1:
        raise TestChangeAuthorityError(
            "test-change new behavior must resolve exactly once in current phase authority"
        )
    _artifact, statement, supersedes = matches[0]
    if old not in supersedes:
        raise TestChangeAuthorityError(
            "current phase authority does not explicitly supersede the old behavior"
        )
    if authorization.expected_change_statement != statement:
        raise TestChangeAuthorityError(
            "test-change statement differs from the exact ratified replacement"
        )


def _verify_selection(
    authorization: TestChangeAuthorization,
    changed_existing_tests: Sequence[str],
) -> None:
    members = authorization.selection.members
    test_ids = [member.test_id for member in members]
    if len(test_ids) != len(set(test_ids)):
        raise TestChangeAuthorityError(
            "test-change authorization must name each existing test exactly once"
        )
    if len(members) > 1 and not authorization.selection.family_id.strip():
        raise TestChangeAuthorityError(
            "a multi-test expectation change requires a named frozen test family"
        )
    canonical_membership = sorted(test_ids)
    supplied_membership = [str(test_id) for test_id in changed_existing_tests]
    if supplied_membership != canonical_membership:
        raise TestChangeAuthorityError(
            "changed_existing_tests must equal the authorization's sorted exact membership"
        )


def _stage_envelope(root: Path, content: bytes, *, label: str) -> Path:
    staging = root / ".staging" / "authority-envelopes"
    _require_private_directory(staging, label="authority envelope staging directory")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{label}-",
        suffix=".tessera.json",
        dir=staging,
    )
    path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return path
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_and_retain_test_change_authorization_locked(
    runs_root: str | Path,
    run_id: str,
    *,
    authorization_path: str | Path,
    human_receipt_path: str | Path,
    validator_receipt_path: str | Path,
    changed_existing_tests: Sequence[str],
    policy: AuthorityPolicy,
    tessera: TesseraCli,
    clock: Callable[[], int] | None = None,
    additional_consumed_nonces: Sequence[str] = (),
) -> StoredTestChangeAuthorization:
    """Verify and publish one bundle while both run-scoped guards are held."""

    root = Path(runs_root)
    store = RunStore(root)
    projection = store.load(run_id)
    if projection.state not in {
        RunState.OPERATIONAL_MATURITY_RATIFIED,
        RunState.BLOCKED,
    }:
        raise TestChangeAuthorityError(
            "test-change authorization may be activated only immediately before a build"
        )
    if not changed_existing_tests:
        raise TestChangeAuthorityError(
            "test-change authorization requires an exact nonempty changed test set"
        )

    document, _ = _read_object(authorization_path, label="test-change authorization")
    try:
        validate_document("test-change-authorization", document)
    except DocumentValidationError as exc:
        raise TestChangeAuthorityError(str(exc)) from exc
    authorization = TestChangeAuthorization.from_dict(document)
    if document != authorization.body():
        raise TestChangeAuthorityError(
            "test-change authorization is not in its unique canonical semantic form"
        )
    expected = {
        "run_id": run_id,
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
    }
    canonical_authorization = authorization.body()
    for field, expected_value in expected.items():
        if canonical_authorization[field] != expected_value:
            raise TestChangeAuthorityError(f"test-change authorization has wrong {field}")
    if authorization.ruling != TEST_CHANGE_RULING:
        raise TestChangeAuthorityError("test-change ruling is not affirmative")
    if authorization.human_authorizer == authorization.validator_ratifier:
        raise TestChangeAuthorityError("test-change human and Validator ratifiers must be distinct")
    artifacts = _retained_phase_artifacts(root, run_id, projection.phase_artifact_digests)
    _verify_replacement(authorization, artifacts)
    _verify_selection(authorization, changed_existing_tests)

    human_bytes = _read_regular_bytes(human_receipt_path, label="human authority receipt")
    validator_bytes = _read_regular_bytes(
        validator_receipt_path,
        label="Validator authority receipt",
    )
    run_dir = root / run_id
    run_evidence = run_dir / "evidence"
    address = authorization.content_digest.removeprefix("sha256:")
    authority_parent = run_evidence / "test-change-authorizations"
    extra_nonces = tuple(str(nonce) for nonce in additional_consumed_nonces)
    if any(not nonce.strip() for nonce in extra_nonces):
        raise TestChangeAuthorityError("additional consumed authority nonces must be nonempty")
    consumed = frozenset(
        (
            *store.consumed_authority_nonces(run_id),
            *_reserved_nonces(authority_parent, exclude_address=address),
            *extra_nonces,
        )
    )
    human_staging: Path | None = None
    validator_staging: Path | None = None
    try:
        human_staging = _stage_envelope(run_dir, human_bytes, label="human-test-change")
        validator_staging = _stage_envelope(
            run_dir,
            validator_bytes,
            label="validator-test-change",
        )
        if (
            _read_regular_bytes(human_staging, label="staged human authority receipt")
            != human_bytes
        ):
            raise AuthorityVerificationError("staged human authority receipt changed before verify")
        if (
            _read_regular_bytes(validator_staging, label="staged Validator authority receipt")
            != validator_bytes
        ):
            raise AuthorityVerificationError(
                "staged Validator authority receipt changed before verify"
            )
        human_receipt = verify_receipt(
            human_staging,
            policy=policy,
            expected_action=RATIFY_ACTION,
            expected_subject_digest=authorization.content_digest,
            expected_run_id=run_id,
            expected_signer_identity=authorization.human_authorizer,
            tessera=tessera,
            clock=clock,
            consumed_nonces=tuple(consumed),
        )
        human = policy.principal(authorization.human_authorizer)
        if human is None or human.kind != "human":
            raise AuthorityVerificationError("test-change human ratifier is not an enrolled human")
        validator_receipt = verify_receipt(
            validator_staging,
            policy=policy,
            expected_action=RATIFY_ACTION,
            expected_subject_digest=authorization.content_digest,
            expected_run_id=run_id,
            expected_signer_identity=authorization.validator_ratifier,
            tessera=tessera,
            clock=clock,
            consumed_nonces=tuple((*consumed, human_receipt.nonce)),
        )
        validator = policy.principal(authorization.validator_ratifier)
        if validator is None or validator.kind != "agent":
            raise AuthorityVerificationError(
                "test-change Validator ratifier is not an enrolled agent"
            )
        if human.public_key == validator.public_key:
            raise AuthorityVerificationError(
                "test-change human and Validator ratifiers share a signing key"
            )
        if digest_bytes(human_bytes) != human_receipt.envelope.envelope_digest:
            raise AuthorityVerificationError(
                "test-change human receipt changed while it was verified"
            )
        if digest_bytes(validator_bytes) != validator_receipt.envelope.envelope_digest:
            raise AuthorityVerificationError(
                "test-change Validator receipt changed while it was verified"
            )
        if (
            _read_regular_bytes(human_staging, label="staged human authority receipt")
            != human_bytes
        ):
            raise AuthorityVerificationError("staged human authority receipt changed during verify")
        if (
            _read_regular_bytes(validator_staging, label="staged Validator authority receipt")
            != validator_bytes
        ):
            raise AuthorityVerificationError(
                "staged Validator authority receipt changed during verify"
            )
    except AuthorityVerificationError as exc:
        raise TestChangeAuthorityError(str(exc)) from exc
    finally:
        if human_staging is not None:
            human_staging.unlink(missing_ok=True)
        if validator_staging is not None:
            validator_staging.unlink(missing_ok=True)

    current = store.load(run_id)
    if current.ledger_head != projection.ledger_head or current.state != projection.state:
        raise TestChangeAuthorityError(
            "run changed while test-change authority was verified; retry from current state"
        )
    directory = authority_parent / address
    human_file = directory / "human-receipt.tessera.json"
    validator_file = directory / "validator-receipt.tessera.json"
    reservation = _canonical_bytes(
        {
            "schema_version": "factory-authority-nonce-reservation/1",
            "authorization_digest": authorization.content_digest,
            "receipt_digests": [
                human_receipt.envelope.envelope_digest,
                validator_receipt.envelope.envelope_digest,
            ],
            "nonces": [human_receipt.nonce, validator_receipt.nonce],
        }
    )
    _retain_bundle_atomically(
        run_dir=run_dir,
        final_directory=directory,
        files={
            "authorization.json": _canonical_bytes(canonical_authorization),
            "human-receipt.tessera.json": human_bytes,
            "validator-receipt.tessera.json": validator_bytes,
            _RESERVATION_FILE: reservation,
        },
    )
    return StoredTestChangeAuthorization(
        authorization=authorization,
        human_receipt=replace(
            human_receipt,
            envelope=replace(human_receipt.envelope, path=human_file),
        ),
        validator_receipt=replace(
            validator_receipt,
            envelope=replace(validator_receipt.envelope, path=validator_file),
        ),
        directory=directory,
    )


def verify_and_retain_test_change_authorization(
    runs_root: str | Path,
    run_id: str,
    *,
    authorization_path: str | Path,
    human_receipt_path: str | Path,
    validator_receipt_path: str | Path,
    changed_existing_tests: Sequence[str],
    policy: AuthorityPolicy,
    tessera: TesseraCli,
    clock: Callable[[], int] | None = None,
    additional_consumed_nonces: Sequence[str] = (),
) -> StoredTestChangeAuthorization:
    """Activate exact permission to change previously correct test expectations.

    The caller supplies a canonical ``TestChangeAuthorization`` plus two Tessera envelopes over
    that exact content address: one from its named enrolled human and one from its distinct named
    enrolled Validator agent. ``changed_existing_tests`` is the caller's mechanically derived
    changed-test set and must equal the authorization's sorted membership exactly. The ruling is
    accepted only for the current run generation, target state, and ratified phase artifacts.

    Verification, replay reservation, and complete-bundle publication are serialized with both
    test-authority admission and lifecycle transitions. The returned digests and two nonces are
    inputs to the immediately following ``BUILDING`` transition; retention alone never advances
    or authorizes the run.
    """

    root = Path(runs_root)
    try:
        resources = ResourceLedger(root / run_id, run_id)
        with _authority_guard(resources.run_dir):
            with resources.run_transition_guard():
                return _verify_and_retain_test_change_authorization_locked(
                    root,
                    run_id,
                    authorization_path=authorization_path,
                    human_receipt_path=human_receipt_path,
                    validator_receipt_path=validator_receipt_path,
                    changed_existing_tests=changed_existing_tests,
                    policy=policy,
                    tessera=tessera,
                    clock=clock,
                    additional_consumed_nonces=additional_consumed_nonces,
                )
    except ResourceLedgerError as exc:
        raise TestChangeAuthorityError(
            f"test-change authority could not serialize with run transitions: {exc}"
        ) from exc


__all__ = [
    "HUMAN_RECEIPT_KEY",
    "RATIFY_ACTION",
    "StoredTestChangeAuthorization",
    "TestChangeAuthorityError",
    "VALIDATOR_RECEIPT_KEY",
    "verify_and_retain_test_change_authorization",
]
