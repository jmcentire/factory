"""Durable, authority-preserving recovery for blocked Factory runs.

This module intentionally does not inspect a Tester suite or hand its output to
Coder.  The Validator supplies a redacted, ordered plan; the supervisor records
the signed plan and schedules a fresh attempt against the same ratified phase
artifacts.  New requirements are never a retry outcome.
"""

from __future__ import annotations

import json
import re
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from factory_core.manifest import digest_obj
from factory_core.provenance import IntentBackreference
from factory_runtime.authority import human_public_keys
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.state import RunProjection, RunState
from factory_runtime.tessera import TesseraVerificationError, VerifiedEnvelope
from factory_runtime.workflow import FactoryWorkflow, WorkflowError

_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_REPAIR_BRIEF_BYTES = 65_536
_MAX_REPAIR_PAYLOAD_BYTES = 24_576
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


class RepairableOutcome(Protocol):
    """The durable public facts a repair campaign may consume.

    ``BuildOutcome`` satisfies this protocol, but the campaign boundary need not
    reconstruct private Validator evidence merely to run a generic external
    attempt launcher.  The launcher re-derives these fields from the verified
    run projection instead.
    """

    @property
    def candidate_digest(self) -> str: ...

    @property
    def tests_digest(self) -> str: ...

    @property
    def projection(self) -> RunProjection: ...

    @property
    def passed(self) -> bool: ...


class RepairPlanner(Protocol):
    """Privileged Validator diagnosis, deliberately separate from Coder input."""

    def __call__(
        self,
        outcome: RepairableOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
    ) -> RepairPlan: ...


AttemptRunner = Callable[[str, Path | None], RepairableOutcome]
"""Runs exactly one fresh immutable attempt using an optional repair brief."""


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
    intent_backreferences: tuple[IntentBackreference, ...]
    failure_signature: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("repair plan summary is required")
        if not self.actions or not all(action.strip() for action in self.actions):
            raise ValueError("repair plan requires at least one ordered action")
        if not self.intent_backreferences:
            raise ValueError("repair plan requires exact intent backreferences")
        if len(self.intent_backreferences) != len(set(self.intent_backreferences)):
            raise ValueError("repair plan repeats an intent backreference")
        if not self.failure_signature.strip():
            raise ValueError("repair plan requires a stable failure signature")


@dataclass(frozen=True)
class RepairBrief:
    """Signed derived work instruction; never a replacement for human authority."""

    run_id: str
    failed_attempt_id: str
    authorized_attempt_id: str
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
            "authorized_attempt_id": self.authorized_attempt_id,
            "predecessor_ledger_head": self.predecessor_ledger_head,
            "phase_artifact_digests": dict(self.phase_artifact_digests),
            "candidate_digest": self.candidate_digest,
            "oracle_digest": self.oracle_digest,
            "summary": self.plan.summary,
            "actions": list(self.plan.actions),
            "intent_backreferences": [
                reference.to_dict() for reference in self.plan.intent_backreferences
            ],
            "failure_signature": self.plan.failure_signature,
        }
        _assert_coder_safe(document)
        try:
            validate_document("repair-brief", document)
        except DocumentValidationError as exc:
            raise RepairSupervisorError(str(exc)) from exc
        if len(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")) > (
            _MAX_REPAIR_PAYLOAD_BYTES
        ):
            raise RepairSupervisorError("repair brief payload exceeds its byte ceiling")
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
    ) -> RepairCampaignResult:
        """Run fresh attempts until preview, a human gate, or the explicit budget.

        ``attempt_runner`` is responsible for executing a Factory attempt.  It
        receives only the signed brief path, never the private Validator/Test
        material used to derive it.
        """

        if not _ATTEMPT_ID.fullmatch(initial_attempt_id):
            raise RepairSupervisorError("initial attempt id is invalid")
        started = self.clock()
        recorded_attempt_ids = set(self.workflow.store.build_attempt_ids(run_id))
        if initial_attempt_id in recorded_attempt_ids:
            raise RepairSupervisorError("initial attempt id was already committed by this run")
        attempt_id = initial_attempt_id
        brief_path: Path | None = None
        briefs: list[Path] = []
        # A campaign may begin after a previously blocked attempt has already
        # received a Validator diagnosis.  Seed the normalized cluster so the
        # first fresh attempt cannot silently re-run the same diagnosis forever.
        signatures: set[str] = set()
        if initial_repair_brief_path is not None:
            try:
                verified = self.workflow.recover_or_verify_repair_brief(
                    run_id,
                    envelope_path=initial_repair_brief_path,
                    validator_identity=self.validator_identity,
                    expected_attempt_id=initial_attempt_id,
                )
            except WorkflowError as exc:
                raise RepairSupervisorError(str(exc)) from exc
            brief_path = verified.envelope.path
            briefs.append(verified.envelope.path)
            signatures.add(str(verified.envelope.payload["failure_signature"]))
        reserved_attempt_ids = {*recorded_attempt_ids, initial_attempt_id}
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
                    if attempt_id in self.workflow.store.build_attempt_ids(run_id):
                        return self._terminal(
                            run_id,
                            attempts_run,
                            briefs,
                            "infrastructure-blocked:launch-failure-after-attempt-admission",
                        )
                    if self._elapsed(started):
                        return self._terminal(run_id, attempts_run, briefs, "repair-budget-elapsed")
                    if launch_repairs >= self.policy.max_validator_launch_repairs:
                        return self._terminal(
                            run_id,
                            attempts_run,
                            briefs,
                            "validator-launch-repair-budget-exhausted",
                        )
                    if validator_repair_launch_failure(attempt_id, str(exc)):
                        launch_repairs += 1
                        if self._elapsed(started):
                            return self._terminal(
                                run_id, attempts_run, briefs, "repair-budget-elapsed"
                            )
                        # No author ran and no build artifact exists. This is an idempotent
                        # relaunch of the same signed attempt, not a new candidate attempt.
                        continue
                disposition = (
                    "user-action-required" if exc.user_action_required else "infrastructure-blocked"
                )
                return self._terminal(run_id, attempts_run, briefs, f"{disposition}:{exc}")
            if self._elapsed(started):
                return self._terminal(run_id, attempts_run, briefs, "repair-budget-elapsed")
            current = self.workflow.store.load(run_id)
            if outcome.projection.run_id != run_id:
                raise RepairSupervisorError("attempt outcome belongs to a different run")
            if outcome.projection.ledger_head != current.ledger_head:
                raise RepairSupervisorError("attempt outcome is stale against the run ledger")
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
            try:
                plan = validator_diagnose(
                    outcome,
                    predecessor_ledger_head=current.ledger_head,
                    phase_artifact_digests=current.phase_artifact_digests,
                )
            except RepairCampaignBlocked as exc:
                return self._terminal(run_id, attempts_run, briefs, f"infrastructure-blocked:{exc}")
            if self._elapsed(started):
                return self._terminal(run_id, attempts_run, briefs, "repair-budget-elapsed")
            digests = self.workflow.store.current_artifact_digests(run_id)
            candidate_digest = str(digests.get("candidate", ""))
            oracle_digest = str(digests.get("acceptance-tests", ""))
            if not candidate_digest or not oracle_digest:
                return self._terminal(run_id, attempts_run, briefs, "repair-subject-unavailable")
            if (
                candidate_digest != outcome.candidate_digest
                or oracle_digest != outcome.tests_digest
            ):
                raise RepairSupervisorError(
                    "attempt outcome candidate/oracle differs from the blocked ledger"
                )
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
                if self._elapsed(started):
                    return self._terminal(run_id, attempts_run, briefs, "repair-budget-elapsed")
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
            authorized_attempt_id = self._reserve_attempt_id(
                next_attempt_id,
                attempts_run + 1,
                reserved_attempt_ids,
            )
            brief = RepairBrief(
                run_id=run_id,
                failed_attempt_id=attempt_id,
                authorized_attempt_id=authorized_attempt_id,
                predecessor_ledger_head=current.ledger_head,
                phase_artifact_digests=current.phase_artifact_digests,
                candidate_digest=candidate_digest,
                oracle_digest=oracle_digest,
                plan=plan,
            )
            envelope_path = self._brief_path(run_id, brief.digest)
            envelope = self._sign_or_reuse_repair_brief(brief, envelope_path)
            self.workflow.record_repair_brief(
                run_id,
                expected_ledger_head=brief.predecessor_ledger_head,
                brief_digest=brief.digest,
                envelope=envelope,
                validator_identity=self.validator_identity,
            )
            briefs.append(envelope.path)
            brief_path = envelope.path
            attempt_id = authorized_attempt_id
        raise AssertionError("repair campaign escaped its attempt budget")

    def _brief_path(self, run_id: str, digest: str) -> Path:
        stem = digest.removeprefix("sha256:")
        return self.workflow.root / run_id / "evidence" / "repair-briefs" / f"{stem}.tessera.json"

    def _sign_or_reuse_repair_brief(
        self,
        brief: RepairBrief,
        envelope_path: Path,
    ) -> VerifiedEnvelope:
        """Create the canonical envelope or authenticate an exact pre-ledger orphan.

        Tessera publication and ledger admission are deliberately separate durable steps.  A
        crash between them can therefore leave a valid canonical envelope with no authority
        event.  Replaying that exact operation must verify and reuse the signed bytes rather
        than overwrite them or permanently wedge the repair campaign.
        """

        principal = self.workflow.policy.principal(self.validator_identity)
        if principal is None or principal.kind != "agent":
            raise RepairSupervisorError("repair signer must be an enrolled Validator agent")
        if envelope_path.exists() or envelope_path.is_symlink():
            return self._verify_reusable_repair_brief(
                envelope_path,
                brief=brief,
                public_key=principal.public_key,
            )
        try:
            envelope = self.workflow.tessera.wrap_json(
                brief.document(),
                kind="factory-repair-brief",
                key_path=self.validator_key_path,
                output_path=envelope_path,
            
                forbidden_signer_public_keys=human_public_keys(self.workflow.policy),
            )
        except TesseraVerificationError as signing_error:
            # Another identical supervisor may have won the no-replace publication race.
            if not envelope_path.exists() and not envelope_path.is_symlink():
                raise RepairSupervisorError(str(signing_error)) from signing_error
            return self._verify_reusable_repair_brief(
                envelope_path,
                brief=brief,
                public_key=principal.public_key,
            )
        if envelope.public_key != principal.public_key:
            raise RepairSupervisorError("signed repair brief does not use the Validator key")
        self._require_regular_bounded_envelope(envelope.path)
        return envelope

    def _verify_reusable_repair_brief(
        self,
        envelope_path: Path,
        *,
        brief: RepairBrief,
        public_key: str,
    ) -> VerifiedEnvelope:
        self._require_regular_bounded_envelope(envelope_path)
        try:
            envelope = self.workflow.tessera.verify_json(
                envelope_path,
                trusted_public_keys=(public_key,),
                expected_kind="factory-repair-brief",
                expected_payload_digest=brief.digest,
            )
        except TesseraVerificationError as exc:
            raise RepairSupervisorError(
                f"existing repair envelope is not an exact authenticated orphan: {exc}"
            ) from exc
        if envelope.public_key != public_key:
            raise RepairSupervisorError("existing repair envelope uses a different Validator key")
        return envelope

    @staticmethod
    def _require_regular_bounded_envelope(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RepairSupervisorError(f"signed repair brief is unreadable: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_REPAIR_BRIEF_BYTES:
            raise RepairSupervisorError(
                "signed repair brief is not regular or exceeds its byte ceiling"
            )

    def _elapsed(self, started: float) -> bool:
        """Observed campaign ceiling; the attempt runner owns its hard per-call ceiling."""

        return self.clock() - started > self.policy.max_elapsed_seconds

    @staticmethod
    def _reserve_attempt_id(
        selector: Callable[[int], str],
        index: int,
        reserved: set[str],
    ) -> str:
        candidate = selector(index)
        if not isinstance(candidate, str) or not _ATTEMPT_ID.fullmatch(candidate):
            raise RepairSupervisorError("next attempt id is invalid")
        if candidate in reserved:
            raise RepairSupervisorError("next attempt id must be fresh within the run")
        reserved.add(candidate)
        return candidate

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
