"""Run-owned resource and contact ledger.

The lifecycle ledger proves authority transitions. This separate ledger proves what an
authorized run contacted or created, and therefore what terminal close is allowed to inspect or
dispose. Records are events: an interrupted ``planned`` event remains visible and blocking rather
than being overwritten by a convenient final status.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from factory_core.manifest import Ledger, LedgerEntry, LedgerIntegrityError, digest_obj
from factory_runtime.schema import DocumentValidationError, validate_document

_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEAL_SCHEMA_VERSION = "factory-resource-ledger-seal/1"
_MAX_METADATA_BYTES = 65_536
_MAX_METADATA_DEPTH = 8
_TERMINAL = frozenset(
    {"succeeded", "failed", "retained", "removed", "unchanged", "abandoned", "disposed"}
)
_ALLOWED_STATUS_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "": frozenset({"planned"}),
    "planned": frozenset({"active", "succeeded", "failed", "unchanged", "abandoned"}),
    "active": frozenset({"retained", "removed", "failed", "compromised", "disposed"}),
    "compromised": frozenset({"retained", "removed", "disposed"}),
    "failed": frozenset({"retained", "removed", "disposed"}),
    "abandoned": frozenset({"retained", "removed", "disposed"}),
}


class ResourceLedgerError(ValueError):
    """Resource ownership, history, or disposition could not be proven."""


def _validate_metadata(value: Mapping[str, Any], *, label: str) -> None:
    """Bound agent-supplied metadata before it becomes permanent ledger growth."""

    def walk(item: Any, depth: int) -> None:
        if depth > _MAX_METADATA_DEPTH:
            raise ResourceLedgerError(f"{label} exceeds maximum nesting depth")
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ResourceLedgerError(f"{label} keys must be strings")
            for nested in item.values():
                walk(nested, depth + 1)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                walk(nested, depth + 1)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ResourceLedgerError(f"{label} contains a non-JSON value")

    walk(value, 0)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResourceLedgerError(f"{label} is not canonical JSON: {exc}") from exc
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ResourceLedgerError(f"{label} exceeds {_MAX_METADATA_BYTES} bytes")


def _validate_disposition(record: Mapping[str, Any], *, context: str) -> None:
    status = str(record["status"])
    ownership = str(record["ownership"])
    disposition = record["disposition"]
    if not isinstance(disposition, Mapping):
        raise ResourceLedgerError(f"{context} disposition must be an object")
    if status in {"failed", "retained", "removed", "abandoned", "disposed"} and not disposition:
        raise ResourceLedgerError(f"{context} terminal status {status!r} requires disposition")
    residue = disposition.get("residue")
    if ownership == "external-non-owned" and status in {
        "retained",
        "removed",
        "disposed",
        "compromised",
    }:
        raise ResourceLedgerError(
            f"{context} may not dispose or claim custody of external/non-owned state"
        )
    if status in {"removed", "disposed", "abandoned"} and residue is not False:
        raise ResourceLedgerError(f"{context} status {status!r} requires residue=false")
    if status == "retained" and residue is not True:
        raise ResourceLedgerError(f"{context} retained status requires residue=true")


class ResourceLedger:
    """Append-only resource events for exactly one Factory run."""

    def __init__(
        self,
        run_dir: str | Path,
        run_id: str,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not _RESOURCE_ID.fullmatch(run_id):
            raise ResourceLedgerError("run_id is not a canonical Factory identifier")
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.path = self.run_dir / "resources.jsonl"
        self.seal_path = self.run_dir / "resources.seal.json"
        self.guard_path = self.run_dir / "resources.guard"
        self.transition_guard_path = self.run_dir / "run-transition.guard"
        self._clock = clock or (lambda: int(time.time()))

    @staticmethod
    def _sync_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path, flags)
        try:
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise ResourceLedgerError("resource-ledger parent must be a directory")
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @contextmanager
    def _exclusive_guard(
        self,
        *,
        path: Path | None = None,
        label: str = "resource guard",
    ) -> Iterator[None]:
        """Serialize resource appends with terminal sealing.

        A seal and an append must observe the same ordering.  The generic ledger append lock
        protects individual JSONL writes; this outer guard protects the cross-file invariant
        that no resource event may be appended after ``resources.seal.json`` is installed.
        A surviving guard is treated as interrupted work and therefore blocks automatically.
        """

        self.run_dir.mkdir(parents=True, exist_ok=True)
        guard_path = path or self.guard_path
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(guard_path, flags, 0o600)
        except FileExistsError as exc:
            raise ResourceLedgerError(
                f"{label} already exists (concurrent or interrupted action): {guard_path}"
            ) from exc
        except OSError as exc:
            raise ResourceLedgerError(f"{label} could not be created: {exc}") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ResourceLedgerError(f"{label} must be a regular file")
            os.write(descriptor, f"pid={os.getpid()}\n".encode())
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            self._sync_directory(self.run_dir)
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(guard_path)
                self._sync_directory(self.run_dir)
            except FileNotFoundError:
                pass

    @contextmanager
    def run_transition_guard(self) -> Iterator[None]:
        """Serialize resource sealing with lifecycle transitions for this run."""

        with self._exclusive_guard(
            path=self.transition_guard_path,
            label="run transition guard",
        ):
            yield

    def _load_seal(self) -> Mapping[str, Any] | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(self.seal_path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ResourceLedgerError(f"resource seal is unreadable: {exc}") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ResourceLedgerError("resource seal must be a regular file")
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                descriptor = -1
                raw = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ResourceLedgerError(f"resource seal is unreadable: {exc}") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(raw, Mapping):
            raise ResourceLedgerError("resource seal must be a JSON object")
        required = {
            "schema_version",
            "run_id",
            "ledger_head",
            "sealed_at",
            "actor",
            "seal_digest",
        }
        if set(raw) != required:
            raise ResourceLedgerError("resource seal has an unknown or missing field")
        body = {key: raw[key] for key in required - {"seal_digest"}}
        if raw["schema_version"] != _SEAL_SCHEMA_VERSION:
            raise ResourceLedgerError("resource seal schema version is unsupported")
        if raw["run_id"] != self.run_id:
            raise ResourceLedgerError("resource seal belongs to another run")
        if not isinstance(raw["ledger_head"], str) or not _DIGEST.fullmatch(
            raw["ledger_head"]
        ):
            raise ResourceLedgerError("resource seal has no canonical ledger head")
        if not isinstance(raw["sealed_at"], int) or raw["sealed_at"] < 1:
            raise ResourceLedgerError("resource seal has no valid timestamp")
        if not isinstance(raw["actor"], str) or not raw["actor"].strip():
            raise ResourceLedgerError("resource seal has no actor")
        expected_digest = digest_obj(body)
        if raw["seal_digest"] != expected_digest:
            raise ResourceLedgerError("resource seal digest does not re-derive")
        return dict(raw)

    def _ledger(self) -> Ledger:
        if self.path.is_symlink():
            raise ResourceLedgerError("resource ledger cannot be a symlink")
        return Ledger(str(self.path))

    def _validated_snapshot(self) -> tuple[list[dict[str, Any]], str]:
        """Validate one resource-ledger snapshot and return its records and exact head."""

        ledger = self._ledger()
        try:
            entries = ledger.verified_entries()
        except LedgerIntegrityError as exc:
            raise ResourceLedgerError(f"resource ledger verification failed: {exc}") from exc
        output: list[dict[str, Any]] = []
        histories: dict[str, list[dict[str, Any]]] = {}
        for index, entry in enumerate(entries):
            if entry.get("capability_id") != self.run_id:
                raise ResourceLedgerError(f"resource entry {index} belongs to another run")
            payload = entry.get("payload")
            if not isinstance(payload, Mapping):
                raise ResourceLedgerError(f"resource entry {index} has no payload object")
            raw = payload.get("resource")
            if not isinstance(raw, Mapping):
                raise ResourceLedgerError(f"resource entry {index} has no resource record")
            record = dict(raw)
            try:
                validate_document("resource-record", record)
            except DocumentValidationError as exc:
                raise ResourceLedgerError(f"resource entry {index}: {exc}") from exc
            if record["run_id"] != self.run_id:
                raise ResourceLedgerError(f"resource entry {index} record belongs to another run")
            resource_id = str(record["resource_id"])
            history = histories.setdefault(resource_id, [])
            prior = history[-1] if history else None
            expected_from = str(prior["status"]) if prior else ""
            if str(entry.get("from_state", "")) != expected_from:
                raise ResourceLedgerError(
                    f"resource entry {index} from_state does not match prior resource status"
                )
            status = str(record["status"])
            if str(entry.get("to_state", "")) != status:
                raise ResourceLedgerError(
                    f"resource entry {index} to_state does not match record status"
                )
            if status not in _ALLOWED_STATUS_TRANSITIONS.get(expected_from, frozenset()):
                raise ResourceLedgerError(
                    f"resource entry {index} records forbidden transition "
                    f"{expected_from or '<new>'} -> {status}"
                )
            if prior:
                for field in (
                    "generation",
                    "resource_type",
                    "identifier",
                    "ownership",
                    "baseline",
                ):
                    if record[field] != prior[field]:
                        raise ResourceLedgerError(
                            f"resource entry {index} changes immutable field {field!r}"
                        )
            _validate_disposition(record, context=f"resource entry {index}")
            history.append(record)
            output.append(record)
        head = str(entries[-1]["entry_hash"]) if entries else ""
        return output, head

    def records(self) -> list[dict[str, Any]]:
        """Verify the chain and every resource event before returning canonical records."""

        records, _ = self._validated_snapshot()
        return records

    def latest(self) -> Mapping[str, Mapping[str, Any]]:
        latest: dict[str, Mapping[str, Any]] = {}
        for record in self.records():
            latest[str(record["resource_id"])] = record
        return latest

    def head(self) -> str:
        _, head = self._validated_snapshot()
        if not head:
            raise ResourceLedgerError("resource ledger has no records")
        return head

    def append(
        self,
        *,
        generation: int,
        resource_id: str,
        resource_type: str,
        identifier: str,
        creator_action: str,
        ownership: str,
        baseline: Mapping[str, Any],
        disposition: Mapping[str, Any],
        status: str,
        evidence_digests: Mapping[str, str] | None = None,
        actor: str,
    ) -> str:
        """Append and durably fsync one resource event.

        The generic Ledger owns the exclusive lock, existing-chain verification, hash link, file
        fsync, and first-create directory fsync. Returning therefore means a child process may be
        invoked without outrunning its ``planned`` record.
        """

        if not _RESOURCE_ID.fullmatch(resource_id):
            raise ResourceLedgerError("resource_id is not a canonical Factory identifier")
        if generation < 1:
            raise ResourceLedgerError("resource generation must be positive")
        has_control = any(
            ord(character) < 32 or ord(character) == 127 for character in identifier
        )
        if not identifier or has_control:
            raise ResourceLedgerError("resource identifier must be non-empty and control-free")
        for label, metadata in (
            ("resource baseline", baseline),
            ("resource disposition", disposition),
            ("resource evidence", evidence_digests or {}),
        ):
            _validate_metadata(metadata, label=label)
        unknown_disposition = set(disposition) - {"reason", "residue"}
        if unknown_disposition:
            raise ResourceLedgerError(
                "resource disposition has unknown field(s): "
                + ", ".join(sorted(str(key) for key in unknown_disposition))
            )
        with self._exclusive_guard():
            if self._load_seal() is not None:
                raise ResourceLedgerError("resource ledger is sealed; no later event is allowed")
            ledger = self._ledger()
            records, expected_head = self._validated_snapshot()
            latest = {str(record["resource_id"]): record for record in records}
            prior = latest.get(resource_id)
            from_status = str(prior["status"]) if prior else ""
            if status not in _ALLOWED_STATUS_TRANSITIONS.get(from_status, frozenset()):
                raise ResourceLedgerError(
                    f"resource status transition refused: {from_status or '<new>'} -> {status}"
                )
            if prior:
                immutable = {
                    "generation": generation,
                    "resource_type": resource_type,
                    "identifier": identifier,
                    "ownership": ownership,
                    "baseline": dict(baseline),
                }
                for field, value in immutable.items():
                    if prior[field] != value:
                        raise ResourceLedgerError(
                            f"resource event changes immutable field {field!r}"
                        )
            event_number = 1 + sum(
                1 for record in records if record["resource_id"] == resource_id
            )
            record_id = f"{resource_id}.{event_number}"
            if not _RESOURCE_ID.fullmatch(record_id):
                record_id = "event-" + digest_obj(
                    {"resource_id": resource_id, "event_number": event_number}
                ).removeprefix("sha256:")
            record = {
                "schema_version": "factory-resource-record/1",
                "record_id": record_id,
                "run_id": self.run_id,
                "generation": generation,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "identifier": identifier,
                "creator_action": creator_action,
                "ownership": ownership,
                "baseline": dict(baseline),
                "disposition": dict(disposition),
                "status": status,
                "evidence_digests": dict(evidence_digests or {}),
                "actor": actor.strip(),
                "created_at": self._clock(),
            }
            try:
                validate_document("resource-record", record)
            except DocumentValidationError as exc:
                raise ResourceLedgerError(str(exc)) from exc
            _validate_disposition(record, context="resource event")
            return ledger.append(
                LedgerEntry(
                    capability_id=self.run_id,
                    from_state=from_status,
                    to_state=status,
                    payload={"resource": record},
                    actor=actor.strip(),
                    created_at=str(record["created_at"]),
                ),
                expected_head=expected_head,
            )

    @staticmethod
    def _require_closeable(
        latest: Mapping[str, Mapping[str, Any]],
    ) -> None:
        unresolved: list[str] = []
        for resource_id, record in latest.items():
            status = str(record["status"])
            ownership = str(record["ownership"])
            disposition = record["disposition"]
            residue = disposition.get("residue") if isinstance(disposition, Mapping) else None
            if status not in _TERMINAL:
                unresolved.append(resource_id)
            elif ownership == "run-owned" and status in {"failed", "abandoned"}:
                if residue is not False:
                    unresolved.append(resource_id)
            elif ownership == "run-owned" and status in {"succeeded", "unchanged"}:
                unresolved.append(resource_id)
            elif ownership == "external-non-owned" and status in {
                "retained",
                "removed",
                "disposed",
            }:
                unresolved.append(resource_id)
        if unresolved:
            raise ResourceLedgerError(
                "run resources have no admissible terminal disposition: "
                + ", ".join(sorted(unresolved))
            )

    def close_snapshot(self) -> tuple[str, Mapping[str, Mapping[str, Any]]]:
        """Return one verified closeable snapshot and the exact head it describes."""

        records, head = self._validated_snapshot()
        if not head:
            raise ResourceLedgerError("run has no resource ledger records")
        latest: dict[str, Mapping[str, Any]] = {}
        for record in records:
            latest[str(record["resource_id"])] = record
        self._require_closeable(latest)
        return head, latest

    def verify_for_close(self) -> Mapping[str, Mapping[str, Any]]:
        """Require a terminal, explicitly disposed latest event for every known resource."""

        _, latest = self.close_snapshot()
        return latest

    def seal_for_close(
        self,
        *,
        actor: str,
        transition_guarded: bool = False,
    ) -> Mapping[str, Any]:
        """Durably bind a closeable head and prevent later resource events.

        The guard makes verification plus seal installation one ordered operation relative to
        every supported append.  A crash after sealing but before promotion is resumable: the
        same valid seal is returned on retry.  A changed ledger, malformed seal, or stale guard
        fails closed instead of silently reopening terminal resource accounting.
        """

        if not actor.strip():
            raise ResourceLedgerError("resource seal actor is required")
        transition_context = nullcontext() if transition_guarded else self.run_transition_guard()
        with transition_context:
            with self._exclusive_guard():
                head, _ = self.close_snapshot()
                existing = self._load_seal()
                if existing is not None:
                    if existing["ledger_head"] != head:
                        raise ResourceLedgerError("resource ledger changed after it was sealed")
                    if existing["actor"] != actor.strip():
                        raise ResourceLedgerError(
                            "resource seal retry actor differs from the sealing actor"
                        )
                    return existing
                body = {
                    "schema_version": _SEAL_SCHEMA_VERSION,
                    "run_id": self.run_id,
                    "ledger_head": head,
                    "sealed_at": self._clock(),
                    "actor": actor.strip(),
                }
                seal = {**body, "seal_digest": digest_obj(body)}
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                try:
                    descriptor = os.open(self.seal_path, flags, 0o600)
                except OSError as exc:
                    raise ResourceLedgerError(
                        f"resource seal could not be created: {exc}"
                    ) from exc
                try:
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise ResourceLedgerError("resource seal must be a regular file")
                    data = (
                        json.dumps(seal, sort_keys=True, separators=(",", ":")) + "\n"
                    ).encode()
                    view = memoryview(data)
                    while view:
                        written = os.write(descriptor, view)
                        if written < 1:
                            raise ResourceLedgerError("resource seal write made no progress")
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._sync_directory(self.run_dir)
                return seal

    def verify_sealed_for_close(
        self,
    ) -> tuple[Mapping[str, Any], Mapping[str, Mapping[str, Any]]]:
        """Require a valid seal over the current closeable resource-ledger head."""

        seal = self._load_seal()
        if seal is None:
            raise ResourceLedgerError("resource ledger has no terminal seal")
        head, latest = self.close_snapshot()
        if seal["ledger_head"] != head:
            raise ResourceLedgerError("resource ledger head differs from its terminal seal")
        return seal, latest

    def terminal_seal(self) -> Mapping[str, Any] | None:
        """Return a verified-present seal, or ``None`` before the close commit point."""

        return self._load_seal()


__all__ = ["ResourceLedger", "ResourceLedgerError"]
