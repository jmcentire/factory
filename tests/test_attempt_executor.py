from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_core.independence import IndependenceRecord
from factory_runtime.attempt import (
    AttemptContractError,
    FactoryAttemptConfig,
    FactoryAttemptExecutor,
    FactoryAttemptInvocation,
)
from factory_runtime.evidence_plane import DeterminismRecord, SurfaceEvidence
from factory_runtime.lanes import LaneRole
from factory_runtime.state import RunProjection, RunState


def _projection() -> RunProjection:
    return RunProjection(
        run_id="run-1",
        state=RunState.PREVIEW,
        target_digest="sha256:" + "a" * 64,
        source_digest="sha256:" + "b" * 64,
        target_state_digest="sha256:" + "c" * 64,
        target_state={},
        generation=1,
        phase_artifact_digests={},
        ledger_head="sha256:" + "d" * 64,
        created_at=1,
        updated_at=1,
    )


def test_typed_executor_calls_only_the_factory_orchestrator(tmp_path: Path) -> None:
    paths = {
        name: tmp_path / name
        for name in (
            "target",
            "catalog",
            "plan",
            "acceptance",
            "acceptance-human",
            "acceptance-validator",
            "coder",
            "tester",
            "validator",
            "checkpoint",
            "genesis",
            "validator-key",
            "repair-brief",
        )
    }
    seen: dict[str, object] = {}

    class Orchestrator:
        def build_and_validate(self, run_id: str, **kwargs: object) -> object:
            seen["run_id"] = run_id
            seen.update(kwargs)
            return SimpleNamespace(
                candidate_digest="sha256:" + "1" * 64,
                tests_digest="sha256:" + "2" * 64,
                projection=_projection(),
                passed=True,
            )

    invocation = FactoryAttemptInvocation(
        target_manifest_path=paths["target"],
        pattern_catalog_path=paths["catalog"],
        build_plan_path=paths["plan"],
        acceptance_catalog_path=paths["acceptance"],
        acceptance_catalog_human_receipt_path=paths["acceptance-human"],
        acceptance_catalog_validator_receipt_path=paths["acceptance-validator"],
        coder_command=(str(paths["coder"]),),
        tester_command=(str(paths["tester"]),),
        validator_command=(str(paths["validator"]),),
        coder_trusted_paths=(paths["coder"],),
        tester_trusted_paths=(paths["tester"],),
        validator_trusted_paths=(paths["validator"],),
        resume_checkpoint_path=paths["checkpoint"],
        expected_resume_checkpoint_digest="sha256:" + "0" * 64,
        genesis_path=paths["genesis"],
        resume_configuration_sources={"attempt": paths["plan"]},
        implementer_identity="agent:coder",
        tester_identity="agent:tester",
        verifier_identity="agent:validator",
        verifier_key_path=paths["validator-key"],
        surface_evidence=(
            SurfaceEvidence("surface", "critical", True, ("acceptance",), {}),
        ),
        determinism_records=(
            DeterminismRecord("surface", "critical", True, 0, 0),
        ),
        lane="capability",
        independence=IndependenceRecord(),
    )

    outcome = FactoryAttemptExecutor(
        SimpleNamespace(),
        invocation=invocation,
        orchestrator=Orchestrator(),  # type: ignore[arg-type]
    ).execute("run-1", attempt_id="attempt-1", repair_brief_path=paths["repair-brief"])

    assert outcome.passed
    assert outcome.candidate_digest == "sha256:" + "1" * 64
    assert seen["run_id"] == "run-1"
    assert seen["attempt_id"] == "attempt-1"
    assert seen["repair_brief_path"] == paths["repair-brief"]
    assert "environment" not in seen


def test_attempt_config_resolves_only_declared_regular_config_sources(tmp_path: Path) -> None:
    source_names = (
        "target",
        "catalog",
        "plan",
        "acceptance",
        "acceptance-human",
        "acceptance-validator",
        "coder-executable",
        "tester-executable",
        "validator-executable",
    )
    sources = {name: tmp_path / name for name in source_names}
    for path in sources.values():
        path.write_text("fixture", encoding="utf-8")
    document = {
        "schema_version": "factory-attempt/1",
        "artifacts": {
            "target_manifest": "target",
            "pattern_catalog": "catalog",
            "build_plan": "plan",
            "acceptance_catalog": "acceptance",
            "acceptance_catalog_human_receipt": "acceptance-human",
            "acceptance_catalog_validator_receipt": "acceptance-validator",
        },
        "roles": {
            role: {
                "identity": f"agent:{role}",
                "executable_source": f"{role}-executable",
                "arguments": [],
                "trusted_path_sources": [f"{role}-executable"],
            }
            for role in ("coder", "tester", "validator")
        },
        "prebuilt_author_outputs": None,
        "surface_evidence": [],
        "determinism_records": [],
        "lane": "capability",
        "independence": {},
        "monitors": [],
        "monitor_declared_unit_count": 0,
    }
    config_path = tmp_path / "attempt.json"
    config_path.write_text(json.dumps(document), encoding="utf-8")

    config = FactoryAttemptConfig.load(config_path, configuration_sources=sources)

    assert config.invocation.coder_command == (str(sources["coder-executable"]),)
    document["artifacts"]["target_manifest"] = "not-bound"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AttemptContractError, match="does not name"):
        FactoryAttemptConfig.load(config_path, configuration_sources=sources)


def test_attempt_config_accepts_sealed_runner_author_outputs(tmp_path: Path) -> None:
    source_names = (
        "target",
        "catalog",
        "plan",
        "acceptance",
        "acceptance-human",
        "acceptance-validator",
        "coder-executable",
        "tester-executable",
        "validator-executable",
    )
    sources = {name: tmp_path / name for name in source_names}
    for path in sources.values():
        path.write_text("fixture", encoding="utf-8")
    coder_output = tmp_path / "coder-output"
    tester_output = tmp_path / "tester-output"
    coder_output.mkdir()
    tester_output.mkdir()
    sources.update({"coder-output": coder_output, "tester-output": tester_output})
    roles = {
        role: {
            "identity": f"agent:{role}",
            "executable_source": f"{role}-executable",
            "arguments": [],
            "trusted_path_sources": [f"{role}-executable"],
        }
        for role in ("coder", "tester", "validator")
    }
    document = {
        "schema_version": "factory-attempt/1",
        "artifacts": {
            "target_manifest": "target",
            "pattern_catalog": "catalog",
            "build_plan": "plan",
            "acceptance_catalog": "acceptance",
            "acceptance_catalog_human_receipt": "acceptance-human",
            "acceptance_catalog_validator_receipt": "acceptance-validator",
        },
        "roles": roles,
        "prebuilt_author_outputs": {"coder": "coder-output", "tester": "tester-output"},
        "surface_evidence": [],
        "determinism_records": [],
        "lane": "capability",
        "independence": {},
        "monitors": [],
        "monitor_declared_unit_count": 0,
    }
    config_path = tmp_path / "attempt.json"
    config_path.write_text(json.dumps(document), encoding="utf-8")

    config = FactoryAttemptConfig.load(config_path, configuration_sources=sources)

    assert config.invocation.coder_command == ()
    assert config.invocation.tester_command == ()
    assert config.invocation.coder_trusted_paths == ()
    assert config.invocation.tester_trusted_paths == ()
    assert config.invocation.prebuilt_author_outputs == {
        LaneRole.CODER: coder_output,
        LaneRole.TESTER: tester_output,
    }
