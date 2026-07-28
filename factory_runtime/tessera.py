"""Actual Tessera CLI integration for signed Factory evidence envelopes.

Tessera supplies cryptographic integrity and replay. Factory supplies the evidence semantics
inside the signed payload. The embedded document public key is never trusted by itself: this
module optionally pins it to an externally supplied trust set before accepting the envelope.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj

_PUBLIC_KEY = re.compile(r"^[0-9a-f]{64}$")


class TesseraVerificationError(ValueError):
    """Tessera rejected an envelope or its Factory binding did not verify."""


@dataclass(frozen=True)
class VerifiedEnvelope:
    """A cryptographically validated Tessera payload bound to its signer and digest."""

    kind: str
    payload: Mapping[str, Any]
    payload_digest: str
    public_key: str
    envelope_digest: str
    path: Path


class TesseraCli:
    """No-shell wrapper over the real Tessera executable."""

    def __init__(
        self,
        command: Sequence[str] = ("tessera",),
        *,
        timeout_seconds: int = 30,
    ) -> None:
        if not command or not all(str(item).strip() for item in command):
            raise TesseraVerificationError("Tessera command cannot be empty")
        self.command = tuple(str(item) for item in command)
        self.timeout_seconds = timeout_seconds

    def wrap_json(
        self,
        payload: Mapping[str, Any],
        *,
        kind: str,
        key_path: str | Path,
        output_path: str | Path,
    ) -> VerifiedEnvelope:
        """Create a new signed JSON envelope without exposing key material to Factory."""

        if not kind.strip():
            raise TesseraVerificationError("envelope kind is required")
        output = Path(output_path)
        if output.is_symlink():
            raise TesseraVerificationError(f"refusing symlink envelope output: {output}")
        if output.exists():
            raise TesseraVerificationError(f"refusing to overwrite envelope: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)

        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_digest = digest_obj(dict(payload))
        schema = {
            "fields": {
                "kind": {"type": "string", "default": kind},
                "payload": {"type": "string", "default": canonical_payload},
                "payload_digest": {"type": "string", "default": payload_digest},
            },
            "mutations": {},
            "api": {"read": ["kind", "payload", "payload_digest"], "write": []},
        }
        fd, temporary = tempfile.mkstemp(
            prefix=".factory-envelope-",
            suffix=".json",
            dir=output.parent,
        )
        envelope_fd, envelope_temporary = tempfile.mkstemp(
            prefix=".factory-signed-",
            suffix=".tessera.json",
            dir=output.parent,
        )
        os.close(envelope_fd)
        os.unlink(envelope_temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(schema, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            self._run(
                [
                    "create",
                    temporary,
                    "--chain-mode",
                    "embedded",
                    "--output",
                    envelope_temporary,
                    "--format",
                    "json",
                    "--key",
                    str(Path(key_path)),
                ]
            )
            verified = self.verify_json(
                envelope_temporary,
                expected_kind=kind,
                expected_payload_digest=payload_digest,
            )
            try:
                os.link(envelope_temporary, output)
            except FileExistsError as exc:
                raise TesseraVerificationError(
                    f"refusing to replace envelope created concurrently: {output}"
                ) from exc
            return VerifiedEnvelope(
                kind=verified.kind,
                payload=verified.payload,
                payload_digest=verified.payload_digest,
                public_key=verified.public_key,
                envelope_digest=verified.envelope_digest,
                path=output,
            )
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
            if os.path.exists(envelope_temporary):
                os.unlink(envelope_temporary)

    def verify_json(
        self,
        envelope_path: str | Path,
        *,
        trusted_public_keys: Sequence[str] = (),
        expected_kind: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> VerifiedEnvelope:
        """Validate Tessera cryptography, then re-derive Factory payload bindings."""

        path = Path(envelope_path)
        try:
            raw_bytes = path.read_bytes()
            document = json.loads(raw_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise TesseraVerificationError(f"unreadable Tessera JSON envelope: {exc}") from exc
        if not isinstance(document, Mapping):
            raise TesseraVerificationError("Tessera envelope must be a JSON object")

        public_key = str(document.get("pubkey", ""))
        if not _PUBLIC_KEY.fullmatch(public_key):
            raise TesseraVerificationError("Tessera envelope has no canonical Ed25519 public key")
        trusted = tuple(trusted_public_keys)
        invalid_trusted_keys = tuple(key for key in trusted if not _PUBLIC_KEY.fullmatch(key))
        if invalid_trusted_keys:
            raise TesseraVerificationError(
                "trusted Tessera public keys must be 64 lowercase hex characters"
            )
        if trusted and not any(hmac.compare_digest(public_key, key) for key in trusted):
            raise TesseraVerificationError("Tessera envelope signer is not in the trusted key set")

        fd, validation_copy = tempfile.mkstemp(
            prefix=".factory-verify-",
            suffix=".tessera.json",
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            self._run(["validate", validation_copy])
        finally:
            if os.path.exists(validation_copy):
                os.unlink(validation_copy)

        state = document.get("state")
        if not isinstance(state, Mapping):
            raise TesseraVerificationError("Tessera envelope has no state object")
        kind = str(state.get("kind", ""))
        encoded_payload = state.get("payload")
        claimed_payload_digest = str(state.get("payload_digest", ""))
        if not kind or not isinstance(encoded_payload, str):
            raise TesseraVerificationError("Tessera envelope is missing its Factory payload fields")
        try:
            payload = json.loads(encoded_payload)
        except json.JSONDecodeError as exc:
            raise TesseraVerificationError(
                f"Tessera Factory payload is not canonical JSON: {exc}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise TesseraVerificationError("Tessera Factory payload must be a JSON object")
        rederived = digest_obj(dict(payload))
        if not hmac.compare_digest(rederived, claimed_payload_digest):
            raise TesseraVerificationError("Tessera Factory payload digest does not re-derive")
        if expected_kind is not None and not hmac.compare_digest(kind, expected_kind):
            raise TesseraVerificationError(
                f"Tessera envelope kind mismatch: expected {expected_kind!r}, got {kind!r}"
            )
        if expected_payload_digest is not None and not hmac.compare_digest(
            rederived,
            expected_payload_digest,
        ):
            raise TesseraVerificationError("Tessera envelope binds a different payload")

        return VerifiedEnvelope(
            kind=kind,
            payload=dict(payload),
            payload_digest=rederived,
            public_key=public_key,
            envelope_digest=digest_bytes(raw_bytes),
            path=path,
        )

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                [*self.command, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TesseraVerificationError(f"Tessera execution failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
            raise TesseraVerificationError(
                f"Tessera refused the envelope ({completed.returncode}): {detail}"
            )
        return completed
