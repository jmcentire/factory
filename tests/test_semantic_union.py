from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.semantic_union import (
    EXTRACTION_SCHEMA,
    RULINGS_SCHEMA,
    SemanticUnionError,
    derive_observation_id,
    load_union,
    render_section,
    update_spec,
    verify_spec,
)


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def extraction(
    raw: bytes,
    *,
    extractor: str,
    question: str = "What exact behavior is required for an existing live row?",
) -> dict[str, object]:
    start = raw.index(b"existing")
    end = raw.index(b" row") + len(b" row")
    return {
        "schema_version": EXTRACTION_SCHEMA,
        "source_kind": "lane-trace",
        "source_id": "coder-two",
        "source_sha256": digest(raw),
        "extractor": {
            "id": extractor,
            "version": "model-v1",
            "configuration_digest": digest(f"config:{extractor}".encode()),
        },
        "items": [
            {
                "start": start,
                "end": end,
                "scope": "hold(existing UBR)",
                "question": question,
            }
        ],
    }


def fixture(
    tmp_path: Path,
    *,
    status: str = "closed",
    second_question: str | None = None,
) -> tuple[Path, Path, bytes, list[str]]:
    artifacts = tmp_path / "artifacts"
    evidence = artifacts / "semantic-evidence"
    for root in (evidence / "sources", evidence / "extractions"):
        for kind in ("planning-pass", "lane-trace", "adversarial-review"):
            (root / kind).mkdir(parents=True, exist_ok=True)
    raw = b"trace asks about existing live row behavior\n"
    (evidence / "sources" / "lane-trace" / "coder-two.source").write_bytes(raw)
    extraction_dir = evidence / "extractions" / "lane-trace" / "coder-two"
    first = extraction(raw, extractor="extractor-a")
    second = extraction(
        raw,
        extractor="extractor-b",
        question=second_question or str(first["items"][0]["question"]),  # type: ignore[index]
    )
    write_json(extraction_dir / "a.json", first)
    write_json(extraction_dir / "b.json", second)

    observation_ids: list[str] = []
    for manifest in (first, second):
        item = manifest["items"][0]  # type: ignore[index]
        observation_id = derive_observation_id(
            source_kind="lane-trace",
            source_id="coder-two",
            source_digest=digest(raw),
            start=int(item["start"]),
            end=int(item["end"]),
            scope=str(item["scope"]),
            question=str(item["question"]),
        )
        if observation_id not in observation_ids:
            observation_ids.append(observation_id)
    rulings = []
    for observation_id in observation_ids:
        if status == "closed":
            rulings.append(
                {
                    "observation_id": observation_id,
                    "status": "closed",
                    "disposition": "resolved",
                    "ruling": "Refuse the operation with the ratified ExistingRow error.",
                    "authority_basis": "Human-ratified Product Specification decision S15.17.",
                    "owner": None,
                    "next_action": None,
                }
            )
        else:
            rulings.append(
                {
                    "observation_id": observation_id,
                    "status": "open",
                    "disposition": "deferred",
                    "ruling": "No authoritative behavior has been selected yet.",
                    "authority_basis": None,
                    "owner": "Validator",
                    "next_action": "Ask the human to choose the observable behavior.",
                }
            )
    write_json(
        evidence / "rulings.json",
        {"schema_version": RULINGS_SCHEMA, "items": rulings},
    )
    spec = artifacts / "product-specification.md"
    spec.write_text("# Product Specification\n", encoding="utf-8")
    return artifacts, spec, raw, observation_ids


def test_exact_evidence_union_materializes_and_verifies(tmp_path: Path) -> None:
    artifacts, spec, _raw, observation_ids = fixture(tmp_path)

    result = update_spec(artifacts, spec)

    assert [item.observation_id for item in result.observations] == observation_ids
    assert result.observations[0].extractors[0].startswith("extractor-a@")
    assert len(result.observations[0].extractors) == 2
    assert (
        result.input_closure["producer_enrollment_coverage"]
        == "unknown-until-producer-inventory-is-joined"
    )
    assert verify_spec(artifacts, spec) == result
    assert "Producer enrollment coverage: `unknown-" in spec.read_text(encoding="utf-8")
    assert render_section(result) in spec.read_text(encoding="utf-8")


def test_every_enrolled_source_requires_two_recorded_extractions(tmp_path: Path) -> None:
    artifacts, _spec, _raw, _ids = fixture(tmp_path)
    extraction_dir = artifacts / "semantic-evidence" / "extractions" / "lane-trace" / "coder-two"
    (extraction_dir / "b.json").unlink()

    with pytest.raises(SemanticUnionError, match="fewer than 2 separately recorded extractions"):
        load_union(artifacts)

    orphan = artifacts / "semantic-evidence" / "sources" / "adversarial-review" / "review.source"
    orphan.write_text("one review finding\n", encoding="utf-8")
    with pytest.raises(SemanticUnionError, match="fewer than 2 separately recorded extractions"):
        load_union(artifacts)


def test_extraction_must_bind_the_source_digest_and_have_distinct_provenance(
    tmp_path: Path,
) -> None:
    artifacts, _spec, _raw, _ids = fixture(tmp_path)
    manifest_path = (
        artifacts / "semantic-evidence" / "extractions" / "lane-trace" / "coder-two" / "a.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_sha256"] = digest(b"different source")
    write_json(manifest_path, manifest)
    with pytest.raises(SemanticUnionError, match="source digest differs"):
        load_union(artifacts)

    artifacts, _spec, _raw, _ids = fixture(tmp_path / "duplicate")
    first_path = (
        artifacts / "semantic-evidence" / "extractions" / "lane-trace" / "coder-two" / "a.json"
    )
    second_path = first_path.with_name("b.json")
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    second["extractor"] = first["extractor"]
    manifest_path = second_path
    manifest = second
    write_json(manifest_path, manifest)
    with pytest.raises(SemanticUnionError, match="duplicate extractor identity"):
        load_union(artifacts)


def test_distinct_extracted_questions_fork_instead_of_collapsing(tmp_path: Path) -> None:
    artifacts, _spec, _raw, observation_ids = fixture(
        tmp_path,
        second_question="Must an existing live row be an idempotent no-op or a refusal?",
    )

    result = load_union(artifacts)

    assert len(observation_ids) == 2
    assert {item.observation_id for item in result.observations} == set(observation_ids)


def test_missing_or_extra_ruling_cannot_disappear_from_the_union(tmp_path: Path) -> None:
    artifacts, _spec, _raw, _ids = fixture(tmp_path)
    path = artifacts / "semantic-evidence" / "rulings.json"
    rulings = json.loads(path.read_text(encoding="utf-8"))
    rulings["items"] = []
    write_json(path, rulings)

    with pytest.raises(SemanticUnionError, match="ruling set differs"):
        load_union(artifacts)


def test_open_item_and_token_presence_cannot_fake_closed_spec(tmp_path: Path) -> None:
    artifacts, spec, _raw, _ids = fixture(tmp_path, status="open")
    update_spec(artifacts, spec)
    with pytest.raises(SemanticUnionError, match="has open items"):
        verify_spec(artifacts, spec)

    artifacts2, spec2, _raw2, _ids2 = fixture(tmp_path / "second")
    update_spec(artifacts2, spec2)
    spec2.write_text(
        spec2.read_text(encoding="utf-8").replace(
            "Refuse the operation", "CLOSED token says refuse the operation"
        ),
        encoding="utf-8",
    )
    with pytest.raises(SemanticUnionError, match="differs from fresh evidence derivation"):
        verify_spec(artifacts2, spec2)


def test_input_closure_drift_and_post_ratification_rewrite_are_refused(tmp_path: Path) -> None:
    artifacts, spec, _raw, _ids = fixture(tmp_path)
    update_spec(artifacts, spec)
    extraction_path = (
        artifacts / "semantic-evidence" / "extractions" / "lane-trace" / "coder-two" / "a.json"
    )
    extraction_path.write_text(extraction_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(SemanticUnionError, match="differs from fresh evidence derivation"):
        verify_spec(artifacts, spec)

    (spec.parent / "product-specification.md.digest").write_text("ratified\n", encoding="utf-8")
    with pytest.raises(SemanticUnionError, match="refusing to rewrite a ratified"):
        update_spec(artifacts, spec)
