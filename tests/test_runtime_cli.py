from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture

from factory_core.manifest import digest_obj
from factory_core.target import load_target_manifest
from factory_runtime.cli import main

DIGEST = "sha256:" + ("a" * 64)


def _artifact(path: Path) -> dict[str, object]:
    document: dict[str, object] = {
        "artifact_id": "product",
        "phase": "product-specification",
        "version": "1",
        "source_digest": DIGEST,
        "human_ratifier": "human:founder",
        "validator_ratifier": "agent:validator",
        "items": [
            {
                "item_id": "product:1",
                "canonical_statement": "The signed behavior is authoritative.",
                "supersedes": [],
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def test_cli_validates_and_content_addresses_runtime_documents(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    path = tmp_path / "product.json"
    document = _artifact(path)

    assert (
        main(
            [
                "validate-document",
                "--schema",
                "phase-artifact",
                "--input",
                str(path),
            ]
        )
        == 0
    )
    validation = json.loads(capsys.readouterr().out)
    assert validation["valid"] is True
    assert validation["digest"] == digest_obj(document)

    assert main(["digest-json", "--input", str(path)]) == 0
    addressed = json.loads(capsys.readouterr().out)
    assert addressed == {"digest": digest_obj(document)}


def test_cli_refuses_a_document_outside_the_closed_schema(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    path = tmp_path / "product.json"
    document = _artifact(path)
    document["ticket_authority"] = "mutable-input"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert (
        main(
            [
                "validate-document",
                "--schema",
                "phase-artifact",
                "--input",
                str(path),
            ]
        )
        == 2
    )
    assert "factory: refused:" in capsys.readouterr().err


def test_cli_inspects_the_target_operational_abi(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    source = Path(__file__).parent / "fixtures" / "synthetic_target" / "target.toml"
    manifest_path = tmp_path / "target.toml"
    manifest_path.write_bytes(source.read_bytes())

    assert main(["inspect-target", "--manifest", str(manifest_path)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    target = load_target_manifest(manifest_path)

    assert inspected["target_id"] == target.target_id
    assert inspected["content_digest"] == target.content_digest
    assert inspected["source_digest"].startswith("sha256:")
    assert inspected["build"] == dict(target.build)
