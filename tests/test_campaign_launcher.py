from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_core.manifest import digest_obj
from factory_runtime.campaign import (
    CampaignAttemptOutcome,
    CampaignLaunchConfig,
    CampaignLauncher,
    CampaignLaunchError,
)
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
        "schema_version": "factory-campaign-launch/1",
        "initial_attempt_id": "attempt-1",
        "next_attempt_prefix": "repair",
        "workdir": str(tmp_path),
        "attempt_command": ["attempt-tool", "--sealed"],
        "diagnose_command": ["diagnose-tool"],
        "max_attempts": 2,
        "max_elapsed_seconds": 60,
        "attempt_timeout_seconds": 30,
        "diagnosis_timeout_seconds": 30,
        **overrides,
    }
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return CampaignLaunchConfig.load(path)


def _launcher(tmp_path: Path, *, state: RunState = RunState.PREVIEW) -> CampaignLauncher:
    workflow = SimpleNamespace(root=tmp_path / "runs", store=_Store(_projection(state=state)))
    return CampaignLauncher(
        workflow,
        validator_identity="agent:validator",
        validator_key_path=tmp_path / "validator.key",
        config=_config(tmp_path),
    )


def test_campaign_launcher_runs_external_attempt_then_rederives_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _launcher(tmp_path)
    calls: list[dict[str, object]] = []

    def run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("factory_runtime.campaign.subprocess.run", run)

    result = launcher.run("run-1")

    assert result.terminal_reason == "preview"
    assert result.attempts_run == 1
    assert calls[0]["command"] == ("attempt-tool", "--sealed")
    environment = calls[0]["env"]
    assert isinstance(environment, dict)
    assert environment["FACTORY_CAMPAIGN_RUN_ID"] == "run-1"
    assert environment["FACTORY_CAMPAIGN_ATTEMPT_ID"] == "attempt-1"
    assert environment["FACTORY_CAMPAIGN_REPAIR_BRIEF"] == ""


def test_campaign_launcher_treats_no_terminal_candidate_as_retriable_launch_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _launcher(tmp_path, state=RunState.OPERATIONAL_MATURITY_RATIFIED)
    monkeypatch.setattr(
        "factory_runtime.campaign.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    result = launcher.run("run-1")

    assert result.terminal_reason.startswith("infrastructure-blocked:attempt command exited 1")
    assert result.repair_brief_paths == ()


def test_validator_diagnosis_is_closed_to_coder_safe_plan_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _launcher(tmp_path, state=RunState.BLOCKED)
    launcher._active_run_id = "run-1"
    launcher.workflow.store.attempt_ids = frozenset({"attempt-1"})
    seen: dict[str, str] = {}

    def run(_command, **kwargs):
        environment = kwargs["env"]
        seen.update(
            {
                key: environment[key]
                for key in environment
                if key.startswith("FACTORY_CAMPAIGN_")
            }
        )
        output = Path(environment["FACTORY_CAMPAIGN_PLAN_OUTPUT"])
        output.parent.mkdir(parents=True)
        output.write_text(
            json.dumps(
                {
                    "summary": "Bind the authenticated turn to its selected target.",
                    "actions": ["Persist the selected target before dispatch."],
                    "intent_backreferences": [
                        {
                            "artifact_id": "architecture",
                            "artifact_digest": "sha256:" + "e" * 64,
                            "item_id": "target-binding",
                            "intent_digest": "sha256:" + "3" * 64,
                        }
                    ],
                    "failure_signature": "selected-target-not-bound",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("factory_runtime.campaign.subprocess.run", run)
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
        mode="diagnose",
    )

    assert plan.failure_signature == "selected-target-not-bound"
    assert "candidate" not in seen
    assert "oracle" not in seen
    assert "stdout" not in seen
    assert "stderr" not in seen
    assert digest_obj(dict(projection.phase_artifact_digests)) not in seen.values()
    assert seen["FACTORY_CAMPAIGN_FAILED_ATTEMPT_ID"] == "repair-10"


def test_campaign_config_refuses_shell_like_or_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "campaign.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "factory-campaign-launch/1",
                "initial_attempt_id": "attempt-1",
                "next_attempt_prefix": "repair",
                "workdir": str(tmp_path),
                "attempt_command": "attempt-tool --unsafe-shell",
                "diagnose_command": ["diagnose-tool"],
                "max_attempts": 1,
                "max_elapsed_seconds": 1,
                "attempt_timeout_seconds": 1,
                "diagnosis_timeout_seconds": 1,
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CampaignLaunchError, match="unexpected fields"):
        CampaignLaunchConfig.load(path)
