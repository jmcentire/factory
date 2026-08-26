"""Durable, typed boundary for a Factory repair campaign.

The campaign owns retry policy, repair-brief signing, and durable progress. It
does not execute operator-provided commands: a Factory-owned attempt executor
performs one already-ratified attempt and a Validator-owned diagnosis provider
returns only the closed, Coder-safe :class:`RepairPlan` that the existing
``RepairSupervisor`` signs and records.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from factory_core.manifest import digest_bytes
from factory_runtime.repair import (
    RepairableOutcome,
    RepairCampaignBlocked,
    RepairCampaignResult,
    RepairPlan,
    RepairPolicy,
    RepairSupervisor,
)
from factory_runtime.state import RunProjection
from factory_runtime.workflow import FactoryWorkflow

_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_CONFIG_BYTES = 65_536
_MAX_ATTEMPTS = 100
_MAX_SECONDS = 86_400
_CONFIG_VERSION = "factory-campaign-launch/2"


class CampaignLaunchError(ValueError):
    """The outer campaign contract is malformed or did not make durable progress."""


@dataclass(frozen=True)
class CampaignLaunchConfig:
    """The target-neutral, checkpoint-bound campaign control contract."""

    initial_attempt_id: str
    next_attempt_prefix: str
    max_attempts: int
    max_elapsed_seconds: int
    source_digest: str

    @classmethod
    def load(cls, path: str | Path) -> CampaignLaunchConfig:
        source = Path(path)
        raw = _read_regular_bounded(source, label="campaign config", maximum=_MAX_CONFIG_BYTES)
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CampaignLaunchError("campaign config is not valid JSON") from exc
        if not isinstance(document, Mapping):
            raise CampaignLaunchError("campaign config must be a JSON object")
        allowed = {
            "schema_version",
            "initial_attempt_id",
            "next_attempt_prefix",
            "max_attempts",
            "max_elapsed_seconds",
        }
        unexpected = sorted(set(document) - allowed)
        if unexpected:
            raise CampaignLaunchError(
                "campaign config has unexpected fields: " + ", ".join(unexpected)
            )
        if document.get("schema_version") != _CONFIG_VERSION:
            raise CampaignLaunchError("campaign config has an unsupported schema_version")
        initial_attempt_id = _attempt_id(document.get("initial_attempt_id"), "initial_attempt_id")
        next_attempt_prefix = _attempt_prefix(document.get("next_attempt_prefix"))
        return cls(
            initial_attempt_id=initial_attempt_id,
            next_attempt_prefix=next_attempt_prefix,
            max_attempts=_positive_int(document.get("max_attempts"), "max_attempts", _MAX_ATTEMPTS),
            max_elapsed_seconds=_positive_int(
                document.get("max_elapsed_seconds"), "max_elapsed_seconds", _MAX_SECONDS
            ),
            source_digest=digest_bytes(raw),
        )

    def next_attempt_id(self, index: int) -> str:
        candidate = f"{self.next_attempt_prefix}-{index}"
        if not _ATTEMPT_ID.fullmatch(candidate):
            raise CampaignLaunchError("next_attempt_prefix cannot produce a valid attempt id")
        return candidate


@dataclass(frozen=True)
class CampaignAttemptOutcome:
    """The public facts re-derived from a terminal Factory projection."""

    attempt_id: str
    candidate_digest: str
    tests_digest: str
    projection: RunProjection
    _passed: bool

    @property
    def passed(self) -> bool:
        return self._passed


class AttemptExecutor(Protocol):
    """Factory-owned execution of one fully bound immutable attempt."""

    def execute(
        self,
        run_id: str,
        *,
        attempt_id: str,
        repair_brief_path: Path | None,
    ) -> CampaignAttemptOutcome: ...


class ValidatorDiagnosisProvider(Protocol):
    """Validator's closed repair-plan synthesis interface."""

    def diagnose(
        self,
        outcome: CampaignAttemptOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
        mode: str,
    ) -> RepairPlan: ...


class CampaignLauncher:
    """Run a bounded attempt/diagnosis campaign without target-specific policy."""

    def __init__(
        self,
        workflow: FactoryWorkflow,
        *,
        validator_identity: str,
        validator_key_path: str | Path,
        config: CampaignLaunchConfig,
        attempt_executor: AttemptExecutor,
        diagnosis_provider: ValidatorDiagnosisProvider,
    ) -> None:
        self.workflow = workflow
        self.validator_identity = validator_identity
        self.validator_key_path = Path(validator_key_path)
        self.config = config
        self.attempt_executor = attempt_executor
        self.diagnosis_provider = diagnosis_provider

    def run(
        self,
        run_id: str,
        *,
        initial_repair_brief_path: str | Path | None = None,
    ) -> RepairCampaignResult:
        supervisor = RepairSupervisor(
            self.workflow,
            validator_identity=self.validator_identity,
            validator_key_path=self.validator_key_path,
            policy=RepairPolicy(
                max_attempts=self.config.max_attempts,
                max_elapsed_seconds=self.config.max_elapsed_seconds,
            ),
        )
        self._active_run_id = run_id
        try:
            return supervisor.run(
                run_id,
                initial_attempt_id=self.config.initial_attempt_id,
                next_attempt_id=self.config.next_attempt_id,
                attempt_runner=self._run_attempt,
                validator_diagnose=self._diagnose,
                validator_escalate=self._escalate,
                initial_repair_brief_path=(
                    Path(initial_repair_brief_path)
                    if initial_repair_brief_path is not None
                    else None
                ),
            )
        finally:
            del self._active_run_id

    def _run_attempt(
        self,
        attempt_id: str,
        repair_brief_path: Path | None,
    ) -> CampaignAttemptOutcome:
        outcome = self.attempt_executor.execute(
            self._active_run_id,
            attempt_id=attempt_id,
            repair_brief_path=repair_brief_path,
        )
        if outcome.attempt_id != attempt_id or outcome.projection.run_id != self._active_run_id:
            raise RepairCampaignBlocked(
                "typed attempt executor returned a result for another attempt or run",
                validator_retriable=True,
            )
        return outcome

    def _diagnose(
        self,
        outcome: RepairableOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
    ) -> RepairPlan:
        if not isinstance(outcome, CampaignAttemptOutcome):
            raise RepairCampaignBlocked("campaign diagnosis received an untyped attempt outcome")
        return self.diagnosis_provider.diagnose(
            outcome,
            predecessor_ledger_head=predecessor_ledger_head,
            phase_artifact_digests=phase_artifact_digests,
            mode="diagnose",
        )

    def _escalate(
        self,
        outcome: RepairableOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
    ) -> RepairPlan:
        if not isinstance(outcome, CampaignAttemptOutcome):
            raise RepairCampaignBlocked("campaign escalation received an untyped attempt outcome")
        return self.diagnosis_provider.diagnose(
            outcome,
            predecessor_ledger_head=predecessor_ledger_head,
            phase_artifact_digests=phase_artifact_digests,
            mode="escalate",
        )


def _read_regular_bounded(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CampaignLaunchError(f"{label} is unreadable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
        raise CampaignLaunchError(f"{label} must be a bounded regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CampaignLaunchError(f"{label} is unreadable") from exc


def _attempt_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ATTEMPT_ID.fullmatch(value):
        raise CampaignLaunchError(f"campaign config {field} is invalid")
    return value


def _attempt_prefix(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 124:
        raise CampaignLaunchError("campaign config next_attempt_prefix is invalid")
    if not _ATTEMPT_ID.fullmatch(f"{value}-1"):
        raise CampaignLaunchError("campaign config next_attempt_prefix is invalid")
    return value


def _positive_int(value: object, field: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise CampaignLaunchError(f"campaign config {field} must be between 1 and {maximum}")
    return value
