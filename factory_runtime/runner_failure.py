"""Verify and durably retain one failed model invocation across the shell boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.durability import fsync_directory, fsync_directory_chain
from factory_runtime.failure_classification import classify_terminal_failure
from factory_runtime.runner import RunnerManifest, RunnerQualification
from factory_runtime.schema import validate_document
from factory_runtime.state_admission import read_stable_regular_bytes, verify_state_capsule

_MAX_ARTIFACT_BYTES = 5_242_880
_MAX_FAILURE_BYTES = 262_144
_MAX_EXECUTABLE_BYTES = 1_073_741_824


class RunnerFailureEvidenceError(ValueError):
    """Failed invocation evidence is incomplete, inconsistent, or unsafe."""


def _read(path: Path, *, label: str, maximum: int = _MAX_ARTIFACT_BYTES) -> bytes:
    try:
        return read_stable_regular_bytes(path, label=label, max_bytes=maximum)
    except ValueError as exc:
        raise RunnerFailureEvidenceError(str(exc)) from exc


def _object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerFailureEvidenceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RunnerFailureEvidenceError(f"{label} is not a JSON object")
    return value


def _require_equal(actual: object, expected: object, *, label: str) -> None:
    if actual != expected:
        raise RunnerFailureEvidenceError(f"failed runner receipt binds different {label}")


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _digest_regular_file(path: Path, *, label: str) -> tuple[str, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RunnerFailureEvidenceError(f"{label} is not a regular file")
        if before.st_size < 1 or before.st_size > _MAX_EXECUTABLE_BYTES:
            raise RunnerFailureEvidenceError(f"{label} has an invalid byte count")
        hasher = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            byte_count += len(chunk)
        after = os.fstat(descriptor)
        installed = os.lstat(path)
        if (
            _file_identity(before) != _file_identity(after)
            or (installed.st_dev, installed.st_ino) != (before.st_dev, before.st_ino)
            or stat.S_ISLNK(installed.st_mode)
            or byte_count != before.st_size
        ):
            raise RunnerFailureEvidenceError(f"{label} changed while being verified")
        return "sha256:" + hasher.hexdigest(), byte_count
    except OSError as exc:
        raise RunnerFailureEvidenceError(f"{label} is unavailable: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _retain_existing_file(
    destination: Path,
    source: Path,
    *,
    expected_digest: str,
    expected_byte_count: int,
    run_root: Path,
) -> None:
    """Copy one verified private snapshot into the durable evidence set."""

    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise RunnerFailureEvidenceError("failure evidence parent is not a real directory")
    pending = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.pending"
    source_fd = -1
    pending_fd = -1
    published = False
    try:
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        source_before = os.fstat(source_fd)
        if not stat.S_ISREG(source_before.st_mode):
            raise RunnerFailureEvidenceError("runner executable snapshot is not regular")
        if source_before.st_size < 1 or source_before.st_size > _MAX_EXECUTABLE_BYTES:
            raise RunnerFailureEvidenceError("runner executable snapshot has invalid size")
        pending_fd = os.open(
            pending,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o500,
        )
        hasher = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            byte_count += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(pending_fd, view)
                if written <= 0:
                    raise OSError("short write while retaining runner executable")
                view = view[written:]
        source_after = os.fstat(source_fd)
        installed = os.lstat(source)
        if (
            _file_identity(source_before) != _file_identity(source_after)
            or (installed.st_dev, installed.st_ino)
            != (source_before.st_dev, source_before.st_ino)
            or stat.S_ISLNK(installed.st_mode)
            or byte_count != source_before.st_size
        ):
            raise RunnerFailureEvidenceError(
                "runner executable snapshot changed while being retained"
            )
        copied_digest = "sha256:" + hasher.hexdigest()
        _require_equal(copied_digest, expected_digest, label="runner executable digest")
        _require_equal(byte_count, expected_byte_count, label="runner executable byte count")
        os.fsync(pending_fd)
        os.close(pending_fd)
        pending_fd = -1
        try:
            os.link(pending, destination, follow_symlinks=False)
            published = True
        except FileExistsError:
            pass
        actual_digest, actual_byte_count = _digest_regular_file(
            destination,
            label=f"retained {destination.name}",
        )
        _require_equal(
            actual_digest,
            expected_digest,
            label=f"retained {destination.name} digest",
        )
        _require_equal(
            actual_byte_count,
            expected_byte_count,
            label=f"retained {destination.name} byte count",
        )
        destination_fd = os.open(
            destination,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        fsync_directory_chain(destination.parent, through=run_root)
    except OSError as exc:
        raise RunnerFailureEvidenceError(
            f"runner executable snapshot could not be retained: {exc}"
        ) from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if pending_fd >= 0:
            os.close(pending_fd)
        try:
            pending.unlink()
        except FileNotFoundError:
            pass
        if published or destination.exists():
            fsync_directory(destination.parent)


def _retain_once(destination: Path, content: bytes, *, run_root: Path) -> None:
    """Publish exact bytes without replacement, durably through the run root."""

    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise RunnerFailureEvidenceError("failure evidence parent is not a real directory")
    pending = destination.parent / f".{destination.name}.{secrets.token_hex(16)}.pending"
    descriptor = -1
    published = False
    try:
        descriptor = os.open(
            pending,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RunnerFailureEvidenceError("failure evidence staging inode is not regular")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(pending, destination, follow_symlinks=False)
            published = True
        except FileExistsError:
            existing = _read(destination, label=f"retained {destination.name}")
            if existing != content:
                raise RunnerFailureEvidenceError(
                    f"retained {destination.name} differs from the verified invocation"
                ) from None
            existing_fd = os.open(
                destination,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                if not stat.S_ISREG(os.fstat(existing_fd).st_mode):
                    raise RunnerFailureEvidenceError(
                        f"retained {destination.name} is not regular"
                    )
                os.fsync(existing_fd)
            finally:
                os.close(existing_fd)
        fsync_directory_chain(destination.parent, through=run_root)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            pending.unlink()
        except FileNotFoundError:
            pass
        if published or destination.exists():
            fsync_directory(destination.parent)


def verify_and_retain_runner_failure(
    *,
    workspace: Path,
    workspace_root: Path,
    evidence_root: Path,
    run_root: Path,
    projection_path: Path,
    task_path: Path,
    manifest_path: Path,
    expected_run_id: str,
    expected_generation: int,
    expected_role: str,
    expected_receipt_id: str,
    expected_target_state_digest: str,
    expected_resume_checkpoint_digest: str,
) -> Mapping[str, Any]:
    """Validate exact failure state, retain it, and return a safe resource detail."""

    run_root = Path(os.path.abspath(run_root))
    workspace_root = Path(os.path.abspath(workspace_root))
    workspace = Path(os.path.abspath(workspace))
    evidence_root = Path(os.path.abspath(evidence_root))
    try:
        if run_root.resolve(strict=True) != run_root:
            raise RunnerFailureEvidenceError("failure evidence run root contains a symlink")
        if evidence_root.resolve(strict=True) != evidence_root:
            raise RunnerFailureEvidenceError("failure evidence destination contains a symlink")
        if workspace_root.resolve(strict=True) != workspace_root:
            raise RunnerFailureEvidenceError("runner workspace root contains a symlink")
        if workspace.resolve(strict=True) != workspace:
            raise RunnerFailureEvidenceError("runner failure workspace contains a symlink")
    except OSError as exc:
        raise RunnerFailureEvidenceError("failure evidence directory is unavailable") from exc
    try:
        evidence_root.relative_to(run_root)
        workspace.relative_to(workspace_root)
    except ValueError as exc:
        raise RunnerFailureEvidenceError(
            "failure evidence source or destination is outside its admitted root"
        ) from exc
    if not run_root.is_dir() or run_root.is_symlink():
        raise RunnerFailureEvidenceError("failure evidence run root is not a real directory")
    if not workspace_root.is_dir() or workspace_root.is_symlink():
        raise RunnerFailureEvidenceError("runner workspace root is not a real directory")

    receipt_path = workspace / "runner-failure-receipt.json"
    diagnostic_path = workspace / "validator-invocation-diagnostic.json"
    state_capsule_path = workspace / "input" / "state-capsule.json"
    qualification_path = workspace / "input" / "runner-qualification.json"
    receipt_raw = _read(receipt_path, label="runner failure receipt", maximum=_MAX_FAILURE_BYTES)
    diagnostic_raw = _read(
        diagnostic_path,
        label="runner invocation diagnostic",
        maximum=_MAX_FAILURE_BYTES,
    )
    state_capsule_raw = _read(state_capsule_path, label="runner state capsule")
    qualification_raw = _read(
        qualification_path,
        label="runner qualification",
        maximum=_MAX_FAILURE_BYTES,
    )
    projection_raw = _read(projection_path, label="runner projection")
    task_raw = _read(task_path, label="runner task")
    manifest_raw = _read(manifest_path, label="runner manifest")

    receipt = _object(receipt_raw, label="runner failure receipt")
    diagnostic = _object(diagnostic_raw, label="runner invocation diagnostic")
    state_capsule = _object(state_capsule_raw, label="runner state capsule")
    qualification_document = _object(qualification_raw, label="runner qualification")
    manifest_document = _object(manifest_raw, label="runner manifest")
    validate_document("runner-failure-receipt", receipt)
    validate_document("runner-invocation-diagnostic", diagnostic)
    verify_state_capsule(
        state_capsule,
        expected_purpose="lane-dispatch",
        expected_run_id=expected_run_id,
        expected_generation=expected_generation,
        expected_role=expected_role,
        expected_target_state_digest=expected_target_state_digest,
        expected_resume_checkpoint_digest=expected_resume_checkpoint_digest,
    )
    manifest = RunnerManifest.from_dict(manifest_document)
    canonical_qualification = json.dumps(
        qualification_document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if qualification_raw != canonical_qualification:
        raise RunnerFailureEvidenceError("runner qualification is not canonical JSON")
    try:
        qualification = RunnerQualification(**qualification_document)
    except TypeError as exc:
        raise RunnerFailureEvidenceError("runner qualification has invalid fields") from exc
    if not qualification.satisfied:
        raise RunnerFailureEvidenceError("runner qualification is not satisfied")

    primary_snapshot = dict(receipt["executable_snapshot"])
    primary_path = workspace / str(primary_snapshot["relative_path"])
    primary_digest, primary_byte_count = _digest_regular_file(
        primary_path,
        label="runner executable snapshot",
    )
    child_snapshot_documents = [
        dict(item) for item in receipt["child_executable_snapshots"]
    ]
    child_snapshots: list[tuple[Path, str, int]] = []
    for index, snapshot in enumerate(child_snapshot_documents, start=1):
        path = workspace / str(snapshot["relative_path"])
        content_digest, byte_count = _digest_regular_file(
            path,
            label=f"runner child executable snapshot {index}",
        )
        child_snapshots.append((path, content_digest, byte_count))

    invocation = int(receipt["invocation"])
    prompt_raws = [
        _read(
            workspace / "input" / f"prompt-{index}.json",
            label=f"runner prompt {index}",
        )
        for index in range(1, invocation + 1)
    ]
    prompt_kinds = ("qualification", "qualification", "task")
    expected_prompt_sequence = [
        {
            "attempt": index,
            "kind": prompt_kinds[index - 1],
            "byte_count": len(raw),
            "content_digest": digest_bytes(raw),
        }
        for index, raw in enumerate(prompt_raws, start=1)
    ]
    first_prompt_raw = prompt_raws[0]
    first_prompt = _object(first_prompt_raw, label="first runner prompt")
    try:
        continuity_nonce = first_prompt["control"]["continuity"]["store_and_echo"]
    except (KeyError, TypeError) as exc:
        raise RunnerFailureEvidenceError("first runner prompt omits the continuity nonce") from exc
    if not isinstance(continuity_nonce, str) or not continuity_nonce:
        raise RunnerFailureEvidenceError("first runner prompt has an invalid continuity nonce")

    dependencies = {
        str(item["dependency_id"]): str(item["content_digest"])
        for item in state_capsule["dependencies"]
    }
    for label, actual, expected in (
        ("run identity", receipt["run_id"], expected_run_id),
        ("generation", receipt["generation"], expected_generation),
        ("role", receipt["role"], expected_role),
        ("receipt identity", receipt["receipt_id"], expected_receipt_id),
        ("target state", receipt["target_state_digest"], expected_target_state_digest),
        (
            "resume checkpoint",
            receipt["resume_checkpoint_digest"],
            expected_resume_checkpoint_digest,
        ),
        ("state capsule run", state_capsule["run_id"], expected_run_id),
        ("state capsule generation", state_capsule["generation"], expected_generation),
        ("state capsule role", state_capsule["role"], expected_role),
        (
            "state capsule target",
            state_capsule["target_state_digest"],
            expected_target_state_digest,
        ),
        (
            "state capsule resume checkpoint",
            state_capsule["resume_checkpoint_digest"],
            expected_resume_checkpoint_digest,
        ),
        ("state capsule digest", receipt["state_capsule_digest"], digest_obj(state_capsule)),
        ("run ledger head", receipt["run_ledger_head"], state_capsule["run_ledger_head"]),
        ("state profile", receipt["state_profile_digest"], state_capsule["profile_digest"]),
        (
            "state qualification",
            receipt["state_qualification_digest"],
            manifest.document["state_qualification_digest"],
        ),
        ("runner manifest", receipt["runner_manifest_digest"], manifest.content_digest),
        (
            "runner manifest source",
            receipt["runner_manifest_source_digest"],
            digest_bytes(manifest_raw),
        ),
        (
            "runner manifest dependency",
            dependencies.get("runner-manifest"),
            digest_bytes(manifest_raw),
        ),
        ("projection", receipt["projection_digest"], digest_bytes(projection_raw)),
        (
            "projection dependency",
            dependencies.get("runner-projection"),
            digest_bytes(projection_raw),
        ),
        ("task", receipt["task_digest"], digest_bytes(task_raw)),
        ("task dependency", dependencies.get("frozen-task"), digest_bytes(task_raw)),
        (
            "broker registry",
            receipt["broker_registry_source_digest"],
            dependencies.get("broker-registry"),
        ),
        ("prompt sequence", receipt["prompt_sequence"], expected_prompt_sequence),
        ("primary executable digest", receipt["executable_digest"], primary_digest),
        (
            "primary executable snapshot digest",
            primary_snapshot["content_digest"],
            primary_digest,
        ),
        (
            "primary executable snapshot byte count",
            primary_snapshot["byte_count"],
            primary_byte_count,
        ),
        ("qualification document", receipt["qualification"], qualification_document),
        (
            "qualification digest",
            receipt["qualification_digest"],
            digest_bytes(qualification_raw),
        ),
        (
            "diagnostic digest",
            receipt["diagnostic"]["content_digest"],
            digest_bytes(diagnostic_raw),
        ),
        ("diagnostic byte count", receipt["diagnostic"]["byte_count"], len(diagnostic_raw)),
        ("diagnostic invocation", diagnostic["invocation"], invocation),
        ("diagnostic return code", diagnostic["returncode"], receipt["returncode"]),
        (
            "diagnostic termination reason",
            diagnostic["termination_reason"],
            receipt["termination_reason"],
        ),
        ("diagnostic process peak", diagnostic["process_peak"], receipt["process_peak"]),
        (
            "continuity nonce",
            receipt["continuity_nonce_digest"],
            digest_obj({"continuity_nonce": continuity_nonce}),
        ),
    ):
        _require_equal(actual, expected, label=label)
    for index, (snapshot, (_, content_digest, byte_count)) in enumerate(
        zip(child_snapshot_documents, child_snapshots, strict=True),
        start=1,
    ):
        _require_equal(
            snapshot["content_digest"],
            content_digest,
            label=f"child executable snapshot {index} digest",
        )
        _require_equal(
            snapshot["byte_count"],
            byte_count,
            label=f"child executable snapshot {index} byte count",
        )
    if int(receipt["model_attempts"]) != invocation:
        raise RunnerFailureEvidenceError("failed runner receipt misstates model attempts")
    expected_failure_capsule = classify_terminal_failure(
        final={"status": "runtime-exception"},
        caller_returncode=int(diagnostic["returncode"]),
        caller_stdout="",
        caller_stderr="",
        validator_result_present=False,
        coder_receipt_present=False,
        tester_receipt_present=False,
        invocation_termination_reason=str(diagnostic["termination_reason"]),
    ).document()
    _require_equal(
        receipt["failure_capsule"],
        expected_failure_capsule,
        label="failure classification",
    )
    for field in (
        "runner_id",
        "adapter",
        "runner_version",
        "model",
        "model_version",
        "configuration_digest",
        "state_profile_digest",
    ):
        _require_equal(receipt[field], manifest.document[field], label=f"manifest {field}")

    destinations = {
        "runner_failure_receipt": evidence_root / "runner-failure-receipt.json",
        "validator_diagnostic": evidence_root / "validator-invocation-diagnostic.json",
        "state_capsule": evidence_root / "failed-state-capsule.json",
        "qualification": evidence_root / "runner-qualification.json",
    }
    for key, content in (
        ("runner_failure_receipt", receipt_raw),
        ("validator_diagnostic", diagnostic_raw),
        ("state_capsule", state_capsule_raw),
        ("qualification", qualification_raw),
    ):
        _retain_once(destinations[key], content, run_root=run_root)
    prompt_evidence: dict[str, str] = {}
    for index, raw in enumerate(prompt_raws, start=1):
        destination = evidence_root / f"failed-prompt-{index}.json"
        _retain_once(destination, raw, run_root=run_root)
        prompt_evidence[f"failed-prompt-{index}"] = digest_bytes(raw)
    _retain_existing_file(
        evidence_root / "runner-executable",
        primary_path,
        expected_digest=primary_digest,
        expected_byte_count=primary_byte_count,
        run_root=run_root,
    )
    child_evidence: dict[str, str] = {}
    for index, (path, content_digest, byte_count) in enumerate(child_snapshots, start=1):
        _retain_existing_file(
            evidence_root / f"runner-child-executable-{index}",
            path,
            expected_digest=content_digest,
            expected_byte_count=byte_count,
            run_root=run_root,
        )
        child_evidence[f"runner-child-executable-{index}"] = content_digest

    return {
        "disposition": {
            "reason": "qualified runner invocation failed",
            "residue": True,
        },
        "evidence_digests": {
            "runner-failure-receipt": digest_bytes(receipt_raw),
            "validator-invocation-diagnostic": digest_bytes(diagnostic_raw),
            "failed-state-capsule": digest_bytes(state_capsule_raw),
            "runner-qualification": digest_bytes(qualification_raw),
            "runner-executable": primary_digest,
            **prompt_evidence,
            **child_evidence,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--role", choices=("coder", "tester", "validator"), required=True)
    parser.add_argument("--receipt-id", required=True)
    parser.add_argument("--target-state-digest", required=True)
    parser.add_argument("--resume-checkpoint-digest", required=True)
    args = parser.parse_args(argv)
    try:
        detail = verify_and_retain_runner_failure(
            workspace=args.workspace,
            workspace_root=args.workspace_root,
            evidence_root=args.evidence_root,
            run_root=args.run_root,
            projection_path=args.projection,
            task_path=args.task,
            manifest_path=args.manifest,
            expected_run_id=args.run_id,
            expected_generation=args.generation,
            expected_role=args.role,
            expected_receipt_id=args.receipt_id,
            expected_target_state_digest=args.target_state_digest,
            expected_resume_checkpoint_digest=args.resume_checkpoint_digest,
        )
    except (OSError, ValueError) as exc:
        print(f"runner failure evidence refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(detail, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
