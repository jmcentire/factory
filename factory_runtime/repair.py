"""Durable, authority-preserving recovery for blocked Factory runs.

This module intentionally does not inspect a Tester suite or hand its output to
Coder.  The Validator supplies a redacted, ordered plan; the supervisor records
the signed plan and schedules a fresh attempt against the same ratified phase
artifacts.  New requirements are never a retry outcome.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from factory_core.manifest import digest_obj
from factory_runtime.orchestrator import BuildOutcome
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunProjection, RunState
from factory_runtime.workflow import FactoryWorkflow

_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_BRIEF_KEYS = frozenset(
    {
        "test_name",
        "test_names",
        "assertion",
        "assertions",
        "fixture",
        "fixtures",
        "trace",
        "traces",
        "stdout",
        "stderr",
        "oracle_details",
    }
)


class RepairSupervisorError(RuntimeError):
    """Recovery cannot advance without weakening authority or confidentiality."""


class RepairCampaignBlocked(RuntimeError):
    """A Validator-owned prerequisite blocks a campaign without blaming Coder.

    This is deliberately not a failed repair plan: no Coder Repair Brief is
    minted, no repair budget is consumed, and the caller receives a durable
    terminal reason it can surface to the operator.
    """

    def __init__(
        self,
        reason: str,
        *,
        validator_retriable: bool = False,
        user_action_required: bool = False,
    ) -> None:
        super().__init__(reason)
        self.validator_retriable = validator_retriable
        self.user_action_required = user_action_required


class RepairPlanner(Protocol):
    """Privileged Validator diagnosis, deliberately separate from Coder input."""

    def __call__(
        self,
        outcome: BuildOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
    ) -> RepairPlan: ...


class AttemptRunner(Protocol):
    """Runs exactly one fresh immutable attempt using an optional repair brief."""

    def __call__(
        self,
        attempt_id: str,
        repair_brief_path: Path | None,
    ) -> BuildOutcome: ...


class ValidatorLaunchRepairer(Protocol):
    """Repairs a public launch prerequisite without involving either author lane."""

    def __call__(self, attempt_id: str, reason: str) -> bool: ...


@dataclass(frozen=True)
class RepairPolicy:
    """Caller-owned recovery budget; no lane may silently shorten it."""

    max_attempts: int
    max_elapsed_seconds: int
    stop_on_repeated_failure_signature: bool = True
    max_repeat_escalations_per_signature: int = 1
    max_validator_launch_repairs: int = 2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.max_elapsed_seconds < 1:
            raise ValueError("max_elapsed_seconds must be positive")
        if self.max_repeat_escalations_per_signature < 0:
            raise ValueError("repeat escalation limit must not be negative")
        if self.max_validator_launch_repairs < 0:
            raise ValueError("validator launch repair limit must not be negative")


@dataclass(frozen=True)
class RepairPlan:
    """Validator-authored, Coder-safe remediation plan.

    ``actions`` are concrete implementation steps expressed in requirement
    language.  They must not contain hidden-test mechanics; the schema check
    below rejects the common structured leakage fields before signing.
    """

    summary: str
    actions: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    failure_signature: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("repair plan summary is required")
        if not self.actions or not all(action.strip() for action in self.actions):
            raise ValueError("repair plan requires at least one ordered action")
        if not self.requirement_ids or not all(item.strip() for item in self.requirement_ids):
            raise ValueError("repair plan requires existing requirement references")
        if not self.failure_signature.strip():
            raise ValueError("repair plan requires a stable failure signature")


@dataclass(frozen=True)
class RepairBrief:
    """Signed derived work instruction; never a replacement for human authority."""

    run_id: str
    failed_attempt_id: str
    predecessor_ledger_head: str
    phase_artifact_digests: Mapping[str, str]
    candidate_digest: str
    oracle_digest: str
    plan: RepairPlan

    def document(self) -> dict[str, Any]:
        document = {
            "schema_version": "factory-repair-brief/1",
            "run_id": self.run_id,
            "failed_attempt_id": self.failed_attempt_id,
            "predecessor_ledger_head": self.predecessor_ledger_head,
            "phase_artifact_digests": dict(self.phase_artifact_digests),
            "candidate_digest": self.candidate_digest,
            "oracle_digest": self.oracle_digest,
            "summary": self.plan.summary,
            "actions": list(self.plan.actions),
            "requirement_ids": list(self.plan.requirement_ids),
            "failure_signature": self.plan.failure_signature,
        }
        _assert_coder_safe(document)
        try:
            validate_document("repair-brief", document)
        except DocumentValidationError as exc:
            raise RepairSupervisorError(str(exc)) from exc
        return document

    @property
    def digest(self) -> str:
        return digest_obj(self.document())


@dataclass(frozen=True)
class RepairCampaignResult:
    projection: RunProjection
    attempts_run: int
    repair_brief_paths: tuple[Path, ...]
    terminal_reason: str


def _assert_coder_safe(value: object) -> None:
    """Reject structured hidden-oracle fields anywhere in a repair brief."""

    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_BRIEF_KEYS.intersection(str(key) for key in value)
        if forbidden:
            raise RepairSupervisorError(
                "repair brief contains Tester-private fields: " + ", ".join(sorted(forbidden))
            )
        for child in value.values():
            _assert_coder_safe(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _assert_coder_safe(child)


class RepairSupervisor:
    """Own retry policy, brief persistence, and campaign-level stop decisions."""

    def __init__(
        self,
        workflow: FactoryWorkflow,
        *,
        validator_identity: str,
        validator_key_path: str | Path,
        policy: RepairPolicy,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workflow = workflow
        self.validator_identity = validator_identity
        self.validator_key_path = Path(validator_key_path)
        self.policy = policy
        self.clock = clock

    def run(
        self,
        run_id: str,
        *,
        initial_attempt_id: str,
        next_attempt_id: Callable[[int], str],
        attempt_runner: AttemptRunner,
        validator_diagnose: RepairPlanner,
        validator_escalate: RepairPlanner | None = None,
        validator_repair_launch_failure: ValidatorLaunchRepairer | None = None,
        initial_repair_brief_path: Path | None = None,
        initial_failure_signature: str | None = None,
        initial_failure_signatures: Sequence[str] = (),
    ) -> RepairCampaignResult:
        """Run fresh attempts until preview, a human gate, or the explicit budget.

        ``attempt_runner`` is responsible for executing a Factory attempt.  It
        receives only the signed brief path, never the private Validator/Test
        material used to derive it.
        """

        if not _ATTEMPT_ID.fullmatch(initial_attempt_id):
            raise RepairSupervisorError("initial attempt id is invalid")
        started = self.clock()
        has_initial_signatures = bool(initial_failure_signature or initial_failure_signatures)
        if (initial_repair_brief_path is None) != (not has_initial_signatures):
            raise RepairSupervisorError(
                "an initial repair brief and normalized failure signature(s) must be "
                "supplied together"
            )
        if initial_repair_brief_path is not None and not initial_repair_brief_path.is_file():
            raise RepairSupervisorError("initial repair brief path does not exist")
        if any(not signature.strip() for signature in initial_failure_signatures):
            raise RepairSupervisorError("initial failure signatures must not be empty")

        attempt_id = initial_attempt_id
        brief_path = initial_repair_brief_path
        briefs: list[Path] = [initial_repair_brief_path] if initial_repair_brief_path else []
        # A campaign may begin after a previously blocked attempt has already
        # received a Validator diagnosis.  Seed the normalized cluster so the
        # first fresh attempt cannot silently re-run the same diagnosis forever.
        signatures = set(initial_failure_signatures)
        if initial_failure_signature:
            signatures.add(initial_failure_signature)
        repeat_escalations: dict[str, int] = {}

        attempts_run = 0
        candidate_attempts = 0
        launch_repairs = 0
        while candidate_attempts < self.policy.max_attempts:
            if self.clock() - started > self.policy.max_elapsed_seconds:
                return self._terminal(run_id, attempts_run, briefs, "repair-budget-elapsed")
            attempts_run += 1
            try:
                outcome = attempt_runner(attempt_id, brief_path)
            except RepairCampaignBlocked as exc:
                # A caller can determine that a launch failed before either
                # author produced an artifact.  There is then no candidate or
                # oracle digest to bind into a RepairBrief, and treating the
                # failure as a Coder repair would invent provenance.
                if exc.validator_retriable and validator_repair_launch_failure is not None:
                    if launch_repairs >= self.policy.max_validator_launch_repairs:
                        return self._terminal(
                            run_id,
                            attempts_run,
                            briefs,
                            "validator-launch-repair-budget-exhausted",
                        )
                    if validator_repair_launch_failure(attempt_id, str(exc)):
                        launch_repairs += 1
                        attempt_id = next_attempt_id(attempts_run + 1)
                        if not _ATTEMPT_ID.fullmatch(attempt_id):
                            raise RepairSupervisorError("next attempt id is invalid") from exc
                        continue
                disposition = (
                    "user-action-required"
                    if exc.user_action_required
                    else "infrastructure-blocked"
                )
                return self._terminal(run_id, attempts_run, briefs, f"{disposition}:{exc}")
            candidate_attempts += 1
            if outcome.passed:
                return RepairCampaignResult(
                    projection=outcome.projection,
                    attempts_run=attempts_run,
                    repair_brief_paths=tuple(briefs),
                    terminal_reason="preview",
                )
            if outcome.projection.state != RunState.BLOCKED:
                raise RepairSupervisorError("failed attempt did not produce a terminal BLOCK")
            # A bounded single-attempt campaign is intentionally driven by an
            # externally supplied, causal repair brief.  Do not manufacture a
            # follow-up brief after consuming its only candidate budget: that
            # would require digests from a state that may already have dropped
            # consumed artifact values, and it would record a diagnosis that
            # cannot be launched by this campaign anyway.
            if candidate_attempts >= self.policy.max_attempts:
                return self._terminal(
                    run_id, attempts_run, briefs, "repair-attempt-budget-exhausted"
                )
            current = self.workflow.store.load(run_id)
            try:
                plan = validator_diagnose(
                    outcome,
                    predecessor_ledger_head=current.ledger_head,
                    phase_artifact_digests=current.phase_artifact_digests,
                )
            except RepairCampaignBlocked as exc:
                return self._terminal(run_id, attempts_run, briefs, f"infrastructure-blocked:{exc}")
            digests = self.workflow.store.current_artifact_digests(run_id)
            repeated_failure = plan.failure_signature in signatures
            if self.policy.stop_on_repeated_failure_signature and repeated_failure:
                escalation_count = repeat_escalations.get(plan.failure_signature, 0)
                if (
                    validator_escalate is None
                    or escalation_count >= self.policy.max_repeat_escalations_per_signature
                ):
                    return self._terminal(
                        run_id, attempts_run, briefs, "repeated-failure-signature"
                    )
                escalated_plan = validator_escalate(
                    outcome,
                    predecessor_ledger_head=current.ledger_head,
                    phase_artifact_digests=current.phase_artifact_digests,
                )
                if escalated_plan.failure_signature == plan.failure_signature:
                    raise RepairSupervisorError(
                        "repeat escalation must supply a distinct failure strategy signature"
                    )
                if escalated_plan.failure_signature in signatures:
                    return self._terminal(
                        run_id, attempts_run, briefs, "repeat-escalation-not-novel"
                    )
                repeat_escalations[plan.failure_signature] = escalation_count + 1
                plan = escalated_plan
            signatures.add(plan.failure_signature)
            brief = RepairBrief(
                run_id=run_id,
                failed_attempt_id=attempt_id,
                predecessor_ledger_head=current.ledger_head,
                phase_artifact_digests=current.phase_artifact_digests,
                # A terminal BLOCK can retain an artifact key with an empty
                # value after the candidate bundle has been consumed by
                # validation.  Empty ledger values are not authority: retain
                # the attempt receipt's verified digest in that case.
                candidate_digest=str(digests.get("candidate") or outcome.candidate_digest),
                oracle_digest=str(digests.get("acceptance-tests") or outcome.tests_digest),
                plan=plan,
            )
            envelope_path = self._brief_path(run_id, brief.digest)
            envelope = self.workflow.tessera.wrap_json(
                brief.document(),
                kind="factory-repair-brief",
                key_path=self.validator_key_path,
                output_path=envelope_path,
            )
            self.workflow.record_repair_brief(
                run_id,
                expected_ledger_head=brief.predecessor_ledger_head,
                brief_digest=brief.digest,
                envelope=envelope,
                validator_identity=self.validator_identity,
            )
            briefs.append(envelope.path)
            brief_path = envelope.path
            attempt_id = next_attempt_id(attempts_run + 1)
            if not _ATTEMPT_ID.fullmatch(attempt_id):
                raise RepairSupervisorError("next attempt id is invalid")
        raise AssertionError("repair campaign escaped its attempt budget")

    def _brief_path(self, run_id: str, digest: str) -> Path:
        stem = digest.removeprefix("sha256:")
        return self.workflow.root / run_id / "evidence" / "repair-briefs" / f"{stem}.tessera.json"

    def _terminal(
        self,
        run_id: str,
        attempts_run: int,
        briefs: list[Path],
        reason: str,
    ) -> RepairCampaignResult:
        projection = self.workflow.store.load(run_id)
        return RepairCampaignResult(
            projection=projection,
            attempts_run=attempts_run,
            repair_brief_paths=tuple(briefs),
            terminal_reason=reason,
        )
