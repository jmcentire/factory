from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_core.provenance import IntentBackreference
from factory_runtime.campaign import (
    CampaignAttemptOutcome,
    CampaignLaunchConfig,
    CampaignLauncher,
    CampaignLaunchError,
)
from factory_runtime.repair import RepairCampaignBlocked, RepairPlan
from factory_runtime.state import RunProjection, RunState


def _projection(*, state: RunState, generation: int = 1) -> RunProjection:
    return RunProjection(
        run_id="run-1",
        state=state,
        target_digest="sha256:" + "a" * 64,
        source_digest="sha256:" + "b" * 64,
        target_state_digest="sha256:" + "c" * 64,
        target_state={},
        generation=generation,
        phase_artifact_digests={
            "product-specification": "sha256:" + "d" * 64,
            "architecture": "sha256:" + "e" * 64,
            "operational-maturity": "sha256:" + "f" * 64,
        },
        ledger_head="sha256:" + "0" * 64,
        created_at=1,
        updated_at=1,
    )


class _Store:
    def __init__(self, projection: RunProjection) -> None:
        self.projection = projection
        self.attempt_ids: frozenset[str] = frozenset()

    def build_attempt_ids(self, _run_id: str) -> frozenset[str]:
        return self.attempt_ids

    def load(self, _run_id: str) -> RunProjection:
        return self.projection

    def current_artifact_digests(self, _run_id: str) -> dict[str, str]:
        return {
            "candidate": "sha256:" + "1" * 64,
            "acceptance-tests": "sha256:" + "2" * 64,
        }


def _config(tmp_path: Path, **overrides: object) -> CampaignLaunchConfig:
    document: dict[str, object] = {
        "schema_version": "factory-campaign-launch/2",
        "initial_attempt_id": "attempt-1",
        "next_attempt_prefix": "repair",
        "max_attempts": 2,
        "max_elapsed_seconds": 60,
        **overrides,
    }
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return CampaignLaunchConfig.load(path)


class _Executor:
    def __init__(self, outcome: CampaignAttemptOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str, Path | None]] = []

    def execute(
        self, run_id: str, *, attempt_id: str, repair_brief_path: Path | None
    ) -> CampaignAttemptOutcome:
        self.calls.append((run_id, attempt_id, repair_brief_path))
        return CampaignAttemptOutcome(
            attempt_id=attempt_id,
            candidate_digest=self.outcome.candidate_digest,
            tests_digest=self.outcome.tests_digest,
            projection=self.outcome.projection,
            _passed=self.outcome.passed,
        )


class _Diagnoser:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def diagnose(self, outcome, *, predecessor_ledger_head, phase_artifact_digests, mode):
        self.calls.append(
            {
                "outcome": outcome,
                "predecessor_ledger_head": predecessor_ledger_head,
                "phase_artifact_digests": phase_artifact_digests,
                "mode": mode,
            }
        )
        return RepairPlan(
            summary="Repair the signed requirement without exposing oracle mechanics.",
            actions=("Use the ratified source of truth.",),
            intent_backreferences=(
                IntentBackreference(
                    artifact_id="architecture",
                    artifact_digest="sha256:" + "e" * 64,
                    item_id="target-binding",
                    intent_digest="sha256:" + "3" * 64,
                ),
            ),
            failure_signature="typed-diagnosis",
        )


def _launcher(
    tmp_path: Path, *, state: RunState = RunState.PREVIEW
) -> tuple[CampaignLauncher, _Executor, _Diagnoser]:
    workflow = SimpleNamespace(root=tmp_path / "runs", store=_Store(_projection(state=state)))
    executor = _Executor(
        CampaignAttemptOutcome(
            attempt_id="attempt-1",
            candidate_digest="sha256:" + "1" * 64,
            tests_digest="sha256:" + "2" * 64,
            projection=workflow.store.projection,
            _passed=state == RunState.PREVIEW,
        )
    )
    diagnoser = _Diagnoser()
    return (
        CampaignLauncher(
        workflow,
        validator_identity="agent:validator",
        validator_key_path=tmp_path / "validator.key",
        config=_config(tmp_path),
        attempt_executor=executor,
        diagnosis_provider=diagnoser,
        ),
        executor,
        diagnoser,
    )


def test_campaign_launcher_runs_typed_attempt_executor_then_rederives_preview(
    tmp_path: Path,
) -> None:
    launcher, executor, _ = _launcher(tmp_path)

    result = launcher.run("run-1")

    assert result.terminal_reason == "preview"
    assert result.attempts_run == 1
    assert executor.calls == [("run-1", "attempt-1", None)]


def test_campaign_launcher_treats_no_terminal_candidate_as_retriable_launch_fault(
    tmp_path: Path,
) -> None:
    launcher, executor, _ = _launcher(tmp_path, state=RunState.OPERATIONAL_MATURITY_RATIFIED)
    def no_terminal_result(*_args, **_kwargs):
        raise RepairCampaignBlocked(
            "typed attempt executor returned without a terminal candidate result",
            validator_retriable=True,
        )

    executor.execute = no_terminal_result

    result = launcher.run("run-1")

    assert result.terminal_reason.startswith("infrastructure-blocked:")
    assert result.repair_brief_paths == ()


def test_validator_diagnosis_is_closed_to_coder_safe_plan_fields(
    tmp_path: Path,
) -> None:
    launcher, _, diagnoser = _launcher(tmp_path, state=RunState.BLOCKED)
    launcher._active_run_id = "run-1"
    projection = _projection(state=RunState.BLOCKED)
    outcome = CampaignAttemptOutcome(
        attempt_id="repair-10",
        candidate_digest="sha256:" + "1" * 64,
        tests_digest="sha256:" + "2" * 64,
        projection=projection,
        _passed=False,
    )

    plan = launcher._diagnose(
        outcome,
        predecessor_ledger_head=projection.ledger_head,
        phase_artifact_digests=projection.phase_artifact_digests,
    )

    assert plan.failure_signature == "typed-diagnosis"
    assert diagnoser.calls[0]["mode"] == "diagnose"
    assert diagnoser.calls[0]["outcome"] == outcome


def test_campaign_config_refuses_shell_like_or_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "campaign.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "factory-campaign-launch/2",
                "initial_attempt_id": "attempt-1",
                "next_attempt_prefix": "repair",
                "max_attempts": 1,
                "max_elapsed_seconds": 1,
                "attempt_command": ["attempt-tool", "--unsafe-shell"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CampaignLaunchError, match="unexpected fields"):
        CampaignLaunchConfig.load(path)
