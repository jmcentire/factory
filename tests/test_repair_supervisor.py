from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_core.provenance import IntentBackreference
from factory_runtime.repair import (
    RepairBrief,
    RepairCampaignBlocked,
    RepairPlan,
    RepairPolicy,
    RepairSupervisor,
    RepairSupervisorError,
)
from factory_runtime.workflow import WorkflowError


def _brief() -> RepairBrief:
    reference = IntentBackreference(
        artifact_id="architecture",
        artifact_digest="sha256:" + ("c" * 64),
        item_id="target-binding",
        intent_digest="sha256:" + ("1" * 64),
    )
    return RepairBrief(
        run_id="run-1",
        failed_attempt_id="attempt-1",
        authorized_attempt_id="attempt-2",
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
            intent_backreferences=(reference,),
            failure_signature="target-binding-missing",
        ),
    )


def test_repair_brief_is_derived_from_existing_authority_and_coder_safe() -> None:
    brief = _brief()

    document = brief.document()

    assert document["schema_version"] == "factory-repair-brief/1"
    assert document["predecessor_ledger_head"] == brief.predecessor_ledger_head
    assert document["authorized_attempt_id"] == "attempt-2"
    assert document["phase_artifact_digests"] == dict(brief.phase_artifact_digests)
    assert document["actions"] == list(brief.plan.actions)
    assert document["intent_backreferences"] == [brief.plan.intent_backreferences[0].to_dict()]
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


def test_repair_supervisor_authenticates_and_reuses_a_preledger_envelope(
    tmp_path: Path,
) -> None:
    brief = _brief()
    public_key = "a" * 64

    class Tessera:
        wrap_calls = 0
        verify_calls = 0

        def wrap_json(self, payload, *, kind, key_path, output_path):
            del key_path
            self.wrap_calls += 1
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"signed repair envelope")
            return SimpleNamespace(
                kind=kind,
                payload=payload,
                payload_digest=brief.digest,
                public_key=public_key,
                envelope_digest="sha256:" + "b" * 64,
                path=path,
            )

        def verify_json(
            self,
            envelope_path,
            *,
            trusted_public_keys,
            expected_kind,
            expected_payload_digest,
        ):
            self.verify_calls += 1
            assert trusted_public_keys == (public_key,)
            assert expected_kind == "factory-repair-brief"
            assert expected_payload_digest == brief.digest
            return SimpleNamespace(
                kind=expected_kind,
                payload=brief.document(),
                payload_digest=brief.digest,
                public_key=public_key,
                envelope_digest="sha256:" + "b" * 64,
                path=Path(envelope_path),
            )

    tessera = Tessera()

    class Workflow:
        root = tmp_path / "runs"
        policy = SimpleNamespace(
            principal=lambda _identity: SimpleNamespace(kind="agent", public_key=public_key)
        )
        store = SimpleNamespace()

    workflow = Workflow()
    workflow.tessera = tessera
    supervisor = RepairSupervisor(
        workflow,  # type: ignore[arg-type]
        validator_identity="agent:validator",
        validator_key_path=tmp_path / "validator.key",
        policy=RepairPolicy(max_attempts=2, max_elapsed_seconds=60),
    )
    path = supervisor._brief_path("run-1", brief.digest)

    created = supervisor._sign_or_reuse_repair_brief(brief, path)
    reused = supervisor._sign_or_reuse_repair_brief(brief, path)

    assert created.path == path
    assert reused.path == path
    assert tessera.wrap_calls == 1
    assert tessera.verify_calls == 1


def test_repair_campaign_block_is_not_a_coder_retry() -> None:
    class Store:
        def build_attempt_ids(self, _run_id):
            return frozenset()

        def load(self, _run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id="run-1",
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
        def build_attempt_ids(self, _run_id):
            return frozenset()

        def load(self, _run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id="run-1",
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
        def build_attempt_ids(self, _run_id):
            return frozenset()

        def load(self, _run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id="run-1",
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
        def build_attempt_ids(self, _run_id):
            return frozenset()

        def load(self, _run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id="run-1",
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
        policy=RepairPolicy(max_attempts=1, max_elapsed_seconds=60, max_validator_launch_repairs=1),
    )
    calls: list[str] = []
    repairs: list[tuple[str, str]] = []

    def run_attempt(attempt_id: str, _brief):
        calls.append(attempt_id)
        if len(calls) == 1:
            raise RepairCampaignBlocked(
                "configured-coding-agent-mismatch", validator_retriable=True
            )
        return __import__("types").SimpleNamespace(passed=True, projection=Store().load("run-1"))

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

    assert calls == ["attempt-1", "attempt-1"]
    assert repairs == [("attempt-1", "configured-coding-agent-mismatch")]
    assert result.attempts_run == 2
    assert result.repair_brief_paths == ()
    assert result.terminal_reason == "preview"


def test_validator_launch_repair_cannot_relaunch_an_admitted_attempt() -> None:
    class Store:
        admitted = False

        def build_attempt_ids(self, _run_id):
            return frozenset({"attempt-1"}) if self.admitted else frozenset()

        def load(self, _run_id):
            from types import SimpleNamespace

            return SimpleNamespace(
                run_id="run-1",
                state=__import__("factory_runtime.state", fromlist=["RunState"]).RunState.BLOCKED,
                ledger_head="sha256:" + "a" * 64,
                phase_artifact_digests={},
            )

    store = Store()

    class Workflow:
        pass

    workflow = Workflow()
    workflow.store = store
    supervisor = RepairSupervisor(
        workflow,  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(max_attempts=1, max_elapsed_seconds=60, max_validator_launch_repairs=1),
    )
    launch_repair_called = False

    def run_attempt(_attempt_id, _brief):
        store.admitted = True
        raise RepairCampaignBlocked("late-launch-failure", validator_retriable=True)

    def repair_launch(_attempt_id, _reason):
        nonlocal launch_repair_called
        launch_repair_called = True
        return True

    result = supervisor.run(
        "run-1",
        initial_attempt_id="attempt-1",
        next_attempt_id=lambda index: f"attempt-{index}",
        attempt_runner=run_attempt,
        validator_diagnose=lambda *_args, **_kwargs: pytest.fail(
            "admitted launch failure must not diagnose Coder"
        ),
        validator_repair_launch_failure=repair_launch,
    )

    assert result.terminal_reason == (
        "infrastructure-blocked:launch-failure-after-attempt-admission"
    )
    assert launch_repair_called is False


def test_retry_attempt_id_must_be_fresh_before_any_brief_is_recorded() -> None:
    from types import SimpleNamespace

    projection = SimpleNamespace(
        run_id="run-1",
        state=__import__("factory_runtime.state", fromlist=["RunState"]).RunState.BLOCKED,
        ledger_head="sha256:" + "a" * 64,
        phase_artifact_digests={
            "product-specification": "sha256:" + "b" * 64,
            "architecture": "sha256:" + "c" * 64,
            "operational-maturity": "sha256:" + "d" * 64,
        },
    )

    class Store:
        def build_attempt_ids(self, _run_id):
            return frozenset()

        def load(self, _run_id):
            return projection

        def current_artifact_digests(self, _run_id):
            return {
                "candidate": "sha256:" + "e" * 64,
                "acceptance-tests": "sha256:" + "f" * 64,
            }

    class Workflow:
        store = Store()

    supervisor = RepairSupervisor(
        Workflow(),  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(max_attempts=2, max_elapsed_seconds=60),
    )
    outcome = SimpleNamespace(
        passed=False,
        projection=projection,
        candidate_digest="sha256:" + "e" * 64,
        tests_digest="sha256:" + "f" * 64,
    )

    with pytest.raises(RepairSupervisorError, match="must be fresh"):
        supervisor.run(
            "run-1",
            initial_attempt_id="attempt-1",
            next_attempt_id=lambda _index: "attempt-1",
            attempt_runner=lambda _attempt_id, _brief: outcome,
            validator_diagnose=lambda *_args, **_kwargs: _brief().plan,
        )


def test_initial_repair_brief_must_be_verified_before_attempt_runner(
    tmp_path: Path,
) -> None:
    class Store:
        def build_attempt_ids(self, _run_id):
            return frozenset()

    class Workflow:
        store = Store()

        def recover_or_verify_repair_brief(self, *_args, **_kwargs):
            raise WorkflowError("Tessera refused unsigned repair brief")

    supervisor = RepairSupervisor(
        Workflow(),  # type: ignore[arg-type]
        validator_identity="validator",
        validator_key_path="unused",
        policy=RepairPolicy(max_attempts=1, max_elapsed_seconds=60),
    )
    called = False

    def run_attempt(_attempt_id, _brief):
        nonlocal called
        called = True
        raise AssertionError("unverified brief reached attempt runner")

    with pytest.raises(RepairSupervisorError, match="unsigned"):
        supervisor.run(
            "run-1",
            initial_attempt_id="attempt-2",
            next_attempt_id=lambda _index: "attempt-3",
            attempt_runner=run_attempt,
            validator_diagnose=lambda *_args, **_kwargs: _brief().plan,
            initial_repair_brief_path=tmp_path / "unsigned.json",
        )
    assert called is False
