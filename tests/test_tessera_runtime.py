from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

import factory_runtime.tessera as tessera_module
from factory_core.manifest import digest_obj
from factory_runtime.tessera import TesseraCli, TesseraVerificationError

PUBLIC_KEY = "a" * 64


def _write_envelope(
    path: Path,
    payload: dict[str, Any],
    *,
    kind: str = "test-kind",
    public_key: str = PUBLIC_KEY,
) -> None:
    path.write_text(
        json.dumps(
            {
                "pubkey": public_key,
                "state": {
                    "kind": kind,
                    "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    "payload_digest": digest_obj(payload),
                },
            }
        ),
        encoding="utf-8",
    )


def _accept_tessera(
    monkeypatch: pytest.MonkeyPatch,
    cli: TesseraCli,
) -> None:
    monkeypatch.setattr(
        cli,
        "_run",
        lambda arguments: subprocess.CompletedProcess(arguments, 0, "", ""),
    )


def test_verify_json_requires_tessera_validation_and_rederives_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "evidence.tessera.json"
    payload = {"answer": 42}
    _write_envelope(path, payload)
    cli = TesseraCli(("tessera-test",))
    called: list[list[str]] = []

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        called.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(cli, "_run", run)
    verified = cli.verify_json(
        path,
        trusted_public_keys=(PUBLIC_KEY,),
        expected_kind="test-kind",
        expected_payload_digest=digest_obj(payload),
    )

    assert len(called) == 1
    assert called[0][0] == "validate"
    validation_copy = Path(called[0][1])
    assert validation_copy != path
    assert not validation_copy.exists()
    assert verified.payload == payload
    assert verified.public_key == PUBLIC_KEY


def test_verify_json_rejects_untrusted_and_malformed_trust_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "evidence.tessera.json"
    _write_envelope(path, {"answer": 42})
    cli = TesseraCli(("tessera-test",))
    _accept_tessera(monkeypatch, cli)

    with pytest.raises(TesseraVerificationError, match="trusted key set"):
        cli.verify_json(path, trusted_public_keys=("b" * 64,))
    with pytest.raises(TesseraVerificationError, match="64 lowercase"):
        cli.verify_json(path, trusted_public_keys=("not-a-public-key",))


def test_verify_json_rejects_payload_digest_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "evidence.tessera.json"
    _write_envelope(path, {"answer": 42})
    document = json.loads(path.read_text(encoding="utf-8"))
    document["state"]["payload"] = json.dumps({"answer": 41})
    path.write_text(json.dumps(document), encoding="utf-8")
    cli = TesseraCli(("tessera-test",))
    _accept_tessera(monkeypatch, cli)

    with pytest.raises(TesseraVerificationError, match="does not re-derive"):
        cli.verify_json(path)


def test_tessera_command_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "evidence.tessera.json"
    _write_envelope(path, {"answer": 42})
    cli = TesseraCli(("tessera-test",))

    def refuse(
        arguments: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(arguments, 2, "", "signature invalid")

    monkeypatch.setattr(subprocess, "run", refuse)

    with pytest.raises(TesseraVerificationError, match="signature invalid"):
        cli.verify_json(path)


def test_wrap_refuses_a_dangling_symlink_output(tmp_path: Path) -> None:
    output = tmp_path / "evidence.tessera.json"
    output.symlink_to(tmp_path / "missing-target")

    with pytest.raises(TesseraVerificationError, match="symlink envelope output"):
        TesseraCli(("tessera-test",)).wrap_json(
            {"answer": 42},
            kind="test-kind",
            key_path=tmp_path / "unused-key",
            output_path=output,
        )


def test_wrap_fsyncs_the_installed_envelope_and_parent_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "evidence.tessera.json"
    cli = TesseraCli(("tessera-test",))

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "create":
            schema = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
            payload = json.loads(schema["fields"]["payload"]["default"])
            kind = schema["fields"]["kind"]["default"]
            destination = Path(arguments[arguments.index("--output") + 1])
            _write_envelope(destination, payload, kind=kind)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(cli, "_run", run)
    real_fsync = os.fsync
    synced: list[tuple[str, int]] = []

    def track_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced.append(("file" if stat.S_ISREG(metadata.st_mode) else "directory", metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(tessera_module.os, "fsync", track_fsync)

    cli.wrap_json(
        {"answer": 42},
        kind="test-kind",
        key_path=tmp_path / "unused-key",
        output_path=output,
    )

    assert ("file", output.stat().st_ino) in synced
    assert ("directory", output.parent.stat().st_ino) in synced


def test_wrap_removes_its_output_when_envelope_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "evidence.tessera.json"
    cli = TesseraCli(("tessera-test",))
    envelope_inode = 0

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal envelope_inode
        if arguments[0] == "create":
            schema = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
            payload = json.loads(schema["fields"]["payload"]["default"])
            kind = schema["fields"]["kind"]["default"]
            destination = Path(arguments[arguments.index("--output") + 1])
            _write_envelope(destination, payload, kind=kind)
            envelope_inode = destination.stat().st_ino
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(cli, "_run", run)
    real_fsync = os.fsync

    def fail_envelope_fsync(descriptor: int) -> None:
        if os.fstat(descriptor).st_ino == envelope_inode:
            raise OSError("injected envelope fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(tessera_module.os, "fsync", fail_envelope_fsync)

    with pytest.raises(TesseraVerificationError, match="durably retain"):
        cli.wrap_json(
            {"answer": 42},
            kind="test-kind",
            key_path=tmp_path / "unused-key",
            output_path=output,
        )

    assert not output.exists()
