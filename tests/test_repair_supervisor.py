from __future__ import annotations

import pytest

from factory_runtime.repair import (
    RepairBrief,
    RepairCampaignBlocked,
    RepairPlan,
    RepairPolicy,
    RepairSupervisor,
    RepairSupervisorError,
)


def _brief() -> RepairBrief:
    return RepairBrief(
        run_id="run-1",
        failed_attempt_id="attempt-1",
        predecessor_ledger_head="sha256:" + ("a" * 64),
        phase_artifact_digests={
            "product-specification": "sha256:" + ("b" * 64),
            "architecture": "sha256:" + ("c" * 64),
            "operational-maturity": "sha256:" + ("d" * 64),
        },
        candidate_digest="sha256:" + ("e" * 64),
        oracle_digest="sha256:" + ("f" * 64),
        plan=RepairPlan(
            summary="Bind every request to the explicitly selected target.",
            actions=(
                "Persist explicit selection in the session control state.",
                "Bind each accepted turn to that selection before dispatch.",
            ),
            requirement_ids=("architecture:target-binding",),
            failure_signature="target-binding-missing",
        ),
    )


def test_repair_brief_is_derived_from_existing_authority_and_coder_safe() -> None:
    brief = _brief()

    document = brief.document()

    assert document["schema_version"] == "factory-repair-brief/1"
    assert document["predecessor_ledger_head"] == brief.predecessor_ledger_head
    assert document["phase_artifact_digests"] == dict(brief.phase_artifact_digests)
    assert document["actions"] == list(brief.plan.actions)
    assert brief.digest.startswith("sha256:")


def test_repair_policy_requires_a_finite_caller_owned_budget() -> None:
    with pytest.raises(ValueError, match="at least one"):
        RepairPolicy(max_attempts=0, max_elapsed_seconds=60)
    with pytest.raises(ValueError, match="positive"):
        RepairPolicy(max_attempts=1, max_elapsed_seconds=0)
    with pytest.raises(ValueError, match="not be negative"):
        RepairPolicy(
            max_attempts=1,
            max_elapsed_seconds=60,
            max_repeat_escalations_per_signature=-1,
        )


def test_repair_brief_refuses_structured_tester_oracle_leakage() -> None:
    brief = _brief()
    unsafe = {
        **brief.document(),
        "trace": "private assertion trace",
    }

    from factory_runtime.repair import _assert_coder_safe

    with pytest.raises(RepairSupervisorError, match="Tester-private"):
        _assert_coder_safe(unsafe)


def test_repair_campaign_block_is_not_a_coder_retry() -> None:
    class Store:
        def load(self, _run_id):
            from types import SimpleNamespace
            return SimpleNamespace(
                state=__import__("factory_runtime.state", fromlist=["RunState"]).RunState.BLOCKED,
                ledger_head="sha256:" + "a" * 64,
                phase_artifact_digests={
                    "product-specification": "sha256:" + "b" * 64,
                    "architecture": "sha256:" + "c" * 64,
                    "operational-maturity": "sha256:" + "d" * 64,
                },
            )

    class Workflow:
        store = Store()

    supervisor = RepairSupervisor(
        Workflow(),  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(max_attempts=3, max_elapsed_seconds=60),
    )
    outcome = __import__("types").SimpleNamespace(
        passed=False,
        projection=Store().load("run-1"),
        candidate_digest="sha256:" + "e" * 64,
        tests_digest="sha256:" + "f" * 64,
    )
    result = supervisor.run(
        "run-1",
        initial_attempt_id="attempt-1",
        next_attempt_id=lambda _index: "attempt-next",
        attempt_runner=lambda _attempt_id, _brief: outcome,
        validator_diagnose=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RepairCampaignBlocked("external-prerequisite")
        ),
    )
    assert result.attempts_run == 1
    assert result.repair_brief_paths == ()
    assert result.terminal_reason == "infrastructure-blocked:external-prerequisite"


def test_pre_author_lane_launch_fault_never_mints_empty_digest_repair_brief() -> None:
    class Store:
        def load(self, _run_id):
            from types import SimpleNamespace
            return SimpleNamespace(
                state=__import__("factory_runtime.state", fromlist=["RunState"]).RunState.BLOCKED,
                ledger_head="sha256:" + "a" * 64,
                phase_artifact_digests={
                    "product-specification": "sha256:" + "b" * 64,
                    "architecture": "sha256:" + "c" * 64,
                    "operational-maturity": "sha256:" + "d" * 64,
                },
            )

    class Workflow:
        store = Store()

    supervisor = RepairSupervisor(
        Workflow(),  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(max_attempts=3, max_elapsed_seconds=60),
    )
    result = supervisor.run(
        "run-1",
        initial_attempt_id="attempt-1",
        next_attempt_id=lambda _index: "attempt-next",
        attempt_runner=lambda _attempt_id, _brief: (_ for _ in ()).throw(
            RepairCampaignBlocked("pre-author-lane-launch-no-artifacts")
        ),
        validator_diagnose=lambda *_args, **_kwargs: pytest.fail(
            "must not diagnose an absent artifact"
        ),
    )

    assert result.attempts_run == 1
    assert result.repair_brief_paths == ()
    assert result.terminal_reason == "infrastructure-blocked:pre-author-lane-launch-no-artifacts"


def test_exhausted_single_attempt_campaign_does_not_mint_an_unlaunchable_brief() -> None:
    class Store:
        def load(self, _run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                state=__import__("factory_runtime.state", fromlist=["RunState"]).RunState.BLOCKED,
                ledger_head="sha256:" + "a" * 64,
                phase_artifact_digests={},
            )

    class Workflow:
        store = Store()

    supervisor = RepairSupervisor(
        Workflow(),  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(max_attempts=1, max_elapsed_seconds=60),
    )
    outcome = __import__("types").SimpleNamespace(
        passed=False,
        projection=Store().load("run-1"),
        candidate_digest="sha256:" + "e" * 64,
        tests_digest="sha256:" + "f" * 64,
    )

    result = supervisor.run(
        "run-1",
        initial_attempt_id="attempt-1",
        next_attempt_id=lambda _index: "attempt-next",
        attempt_runner=lambda _attempt_id, _brief: outcome,
        validator_diagnose=lambda *_args, **_kwargs: pytest.fail(
            "exhausted campaign must not diagnose"
        ),
    )

    assert result.attempts_run == 1
    assert result.repair_brief_paths == ()
    assert result.terminal_reason == "repair-attempt-budget-exhausted"


def test_validator_retries_its_own_launch_configuration_without_coder_budget() -> None:
    class Store:
        def load(self, _run_id):
            from types import SimpleNamespace
            return SimpleNamespace(
                state=__import__("factory_runtime.state", fromlist=["RunState"]).RunState.PREVIEW,
                ledger_head="sha256:" + "a" * 64,
                phase_artifact_digests={},
            )

    class Workflow:
        store = Store()

    supervisor = RepairSupervisor(
        Workflow(),  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(
            max_attempts=1, max_elapsed_seconds=60, max_validator_launch_repairs=1
        ),
    )
    calls: list[str] = []
    repairs: list[tuple[str, str]] = []

    def run_attempt(attempt_id: str, _brief):
        calls.append(attempt_id)
        if len(calls) == 1:
            raise RepairCampaignBlocked(
                "configured-coding-agent-mismatch", validator_retriable=True
            )
        return __import__("types").SimpleNamespace(
            passed=True, projection=Store().load("run-1")
        )

    result = supervisor.run(
        "run-1",
        initial_attempt_id="attempt-1",
        next_attempt_id=lambda index: f"attempt-{index}",
        attempt_runner=run_attempt,
        validator_diagnose=lambda *_args, **_kwargs: pytest.fail(
            "launch repair must not diagnose Coder"
        ),
        validator_repair_launch_failure=(
            lambda attempt_id, reason: repairs.append((attempt_id, reason)) or True
        ),
    )

    assert calls == ["attempt-1", "attempt-2"]
    assert repairs == [("attempt-1", "configured-coding-agent-mismatch")]
    assert result.attempts_run == 2
    assert result.repair_brief_paths == ()
    assert result.terminal_reason == "preview"
