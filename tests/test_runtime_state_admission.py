from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import digest_bytes
from factory_runtime.state_admission import (
    StateAdmissionError,
    derive_state_capsule,
    profile_digest,
    read_stable_regular_bytes,
    verify_state_capsule,
)


def _dependencies() -> dict[str, bytes]:
    return {
        "target-state": b'{"target":"verified"}',
        "run-ledger-head": ("sha256:" + "1" * 64).encode(),
        "phase-artifact-digests": b'{"architecture":"sha256:' + b"2" * 64 + b'"}',
        "phase-artifact-product-specification": b'{"phase":"product-specification"}',
        "phase-artifact-architecture": b'{"phase":"architecture"}',
        "phase-artifact-operational-maturity": b'{"phase":"operational-maturity"}',
        "frozen-task": b"Implement the ratified behavior.",
        "runner-projection": b'{"files":[]}',
        "role-primer": b"Context only; never authority.",
        "effective-directives": b'{"directives":[]}',
        "directive-readback": b'{"directives":[]}',
        "role-contract": b'{"role":"coder"}',
        "runner-manifest": b'{"runner":"codex"}',
        "runner-output-schema": b'{"type":"object"}',
        "broker-registry": b'{"operations":[]}',
        "resume-checkpoint": b'{"checkpoint":"signed"}',
        "resume-verification": b'{"verified":true}',
        "configuration-set": b'{"runner":"sha256:' + b"3" * 64 + b'"}',
        "state-qualification-observations": b'{"observations":[]}',
        "state-qualification-report": b'{"qualified":true}',
    }


def _capsule(dependencies: dict[str, bytes] | None = None) -> dict[str, Any]:
    return derive_state_capsule(
        purpose="lane-dispatch",
        run_id="run-1",
        generation=3,
        role="coder",
        target_state_digest="sha256:" + "4" * 64,
        run_ledger_head="sha256:" + "5" * 64,
        resume_checkpoint_digest="sha256:" + "6" * 64,
        dependencies=dependencies or _dependencies(),
    )


def test_capsule_is_deterministic_closed_and_path_free(tmp_path: Path) -> None:
    dependencies = _dependencies()
    first = _capsule(dependencies)
    second = _capsule(dict(reversed(list(dependencies.items()))))

    assert first == second
    assert first["profile_digest"] == profile_digest("lane-dispatch")
    encoded = str(first)
    assert str(tmp_path) not in encoded
    assert "Context only" not in encoded
    verify_state_capsule(
        first,
        expected_purpose="lane-dispatch",
        expected_run_id="run-1",
        expected_generation=3,
        expected_role="coder",
        expected_target_state_digest="sha256:" + "4" * 64,
        expected_run_ledger_head="sha256:" + "5" * 64,
        expected_resume_checkpoint_digest="sha256:" + "6" * 64,
        expected_dependencies=dependencies,
    )


@pytest.mark.parametrize("dependency_id", sorted(_dependencies()))
def test_every_missing_dependency_is_refused(dependency_id: str) -> None:
    dependencies = _dependencies()
    del dependencies[dependency_id]

    with pytest.raises(StateAdmissionError, match="missing") as error:
        _capsule(dependencies)

    assert error.value.code == "MISSING_DEPENDENCY"
    assert error.value.dependency_id == dependency_id


def test_unknown_or_hybrid_membership_is_refused() -> None:
    dependencies = _dependencies()
    dependencies["context-and-policy"] = b"ambiguous hybrid"

    with pytest.raises(StateAdmissionError, match="unknown") as error:
        _capsule(dependencies)

    assert error.value.code == "UNKNOWN_DEPENDENCY"


def test_changed_dependency_bytes_invalidate_the_capsule() -> None:
    original = _dependencies()
    capsule = _capsule(original)
    changed = dict(original)
    changed["role-primer"] = b"different context"

    with pytest.raises(StateAdmissionError, match="exact retained"):
        verify_state_capsule(capsule, expected_dependencies=changed)


def test_duplicate_dependency_identity_is_refused_even_with_rehashed_set() -> None:
    capsule = _capsule()
    duplicate = dict(capsule["dependencies"][0])
    capsule["dependencies"].append(duplicate)
    from factory_core.manifest import digest_obj

    capsule["dependency_set_digest"] = digest_obj(capsule["dependencies"])

    with pytest.raises(StateAdmissionError, match="repeats"):
        verify_state_capsule(capsule)


def test_trust_escalation_is_refused_even_with_rehashed_set() -> None:
    capsule = _capsule()
    primer = next(
        item for item in capsule["dependencies"] if item["dependency_id"] == "role-primer"
    )
    primer["trust_class"] = "authority-reference"
    from factory_core.manifest import digest_obj

    capsule["dependency_set_digest"] = digest_obj(capsule["dependencies"])

    with pytest.raises(StateAdmissionError, match="misclassifies"):
        verify_state_capsule(capsule)


def test_oversized_primer_is_refused_before_capsule_exists() -> None:
    dependencies = _dependencies()
    dependencies["role-primer"] = b"x" * 262_145

    with pytest.raises(StateAdmissionError, match="exceeds") as error:
        _capsule(dependencies)

    assert error.value.code == "OVERSIZED_DEPENDENCY"


def test_aggregate_dependency_ceiling_is_enforced() -> None:
    dependencies = _dependencies()
    for phase in (
        "product-specification",
        "architecture",
        "operational-maturity",
    ):
        dependencies[f"phase-artifact-{phase}"] = b"x" * 1_048_576
    dependencies["frozen-task"] = b"x" * 2_097_152

    with pytest.raises(StateAdmissionError, match="total byte ceiling") as error:
        _capsule(dependencies)

    assert error.value.code == "OVERSIZED_DEPENDENCY_SET"


def test_stable_reader_refuses_symlink_and_returns_exact_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"exact")
    link = tmp_path / "link"
    link.symlink_to(source)

    assert read_stable_regular_bytes(source, label="fixture", max_bytes=5) == b"exact"
    assert digest_bytes(b"exact") == digest_bytes(
        read_stable_regular_bytes(source, label="fixture", max_bytes=5)
    )
    source.write_bytes(b"oversized")
    with pytest.raises(StateAdmissionError, match="exceeds 5 bytes") as oversized:
        read_stable_regular_bytes(source, label="fixture", max_bytes=5)
    assert oversized.value.code == "OVERSIZED_DEPENDENCY"
    with pytest.raises(StateAdmissionError, match="opened safely"):
        read_stable_regular_bytes(link, label="fixture", max_bytes=5)


def test_stable_reader_refuses_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    with pytest.raises(StateAdmissionError, match="regular file") as error:
        read_stable_regular_bytes(fifo, label="fixture", max_bytes=5)

    assert error.value.code == "DEPENDENCY_NOT_REGULAR"
