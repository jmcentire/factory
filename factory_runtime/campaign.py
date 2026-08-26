"""Generic, durable command boundary for a Factory repair campaign.

The core runtime deliberately does not know a target's model prompts, test
framework, or transport.  This module supplies the missing outer loop without
putting any of that policy in Factory: an operator-provided attempt command
drives one already-ratified attempt, and a Validator-provided diagnosis command
returns only the closed, Coder-safe :class:`RepairPlan` that the existing
``RepairSupervisor`` signs and records.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from factory_core.manifest import digest_bytes
from factory_core.provenance import IntentBackreference
from factory_runtime.repair import (
    RepairCampaignBlocked,
    RepairCampaignResult,
    RepairPlan,
    RepairPolicy,
    RepairSupervisor,
)
from factory_runtime.state import RunProjection, RunState
from factory_runtime.workflow import FactoryWorkflow

_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_CONFIG_BYTES = 65_536
_MAX_PLAN_BYTES = 24_576
_MAX_COMMAND_ITEMS = 64
_MAX_COMMAND_ITEM_BYTES = 16_384
_MAX_ATTEMPTS = 100
_MAX_SECONDS = 86_400
_CONFIG_VERSION = "factory-campaign-launch/1"


class CampaignLaunchError(ValueError):
    """The outer campaign contract is malformed or did not make durable progress."""


@dataclass(frozen=True)
class CampaignLaunchConfig:
    """The target-neutral, operator-owned campaign transport contract.

    The command arrays are argv, never shell source.  They are intentionally
    outside the ratified product authority: they decide how a pre-ratified
    attempt reaches Factory's runtime, not what Coder may implement.
    """

    initial_attempt_id: str
    next_attempt_prefix: str
    workdir: Path
    attempt_command: tuple[str, ...]
    diagnose_command: tuple[str, ...]
    escalate_command: tuple[str, ...] | None
    validator_launch_repair_command: tuple[str, ...] | None
    max_attempts: int
    max_elapsed_seconds: int
    attempt_timeout_seconds: int
    diagnosis_timeout_seconds: int
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
            "workdir",
            "attempt_command",
            "diagnose_command",
            "escalate_command",
            "validator_launch_repair_command",
            "max_attempts",
            "max_elapsed_seconds",
            "attempt_timeout_seconds",
            "diagnosis_timeout_seconds",
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
        workdir = _workdir(document.get("workdir"))
        return cls(
            initial_attempt_id=initial_attempt_id,
            next_attempt_prefix=next_attempt_prefix,
            workdir=workdir,
            attempt_command=_command(document.get("attempt_command"), "attempt_command"),
            diagnose_command=_command(document.get("diagnose_command"), "diagnose_command"),
            escalate_command=_optional_command(
                document.get("escalate_command"), "escalate_command"
            ),
            validator_launch_repair_command=_optional_command(
                document.get("validator_launch_repair_command"),
                "validator_launch_repair_command",
            ),
            max_attempts=_positive_int(document.get("max_attempts"), "max_attempts", _MAX_ATTEMPTS),
            max_elapsed_seconds=_positive_int(
                document.get("max_elapsed_seconds"), "max_elapsed_seconds", _MAX_SECONDS
            ),
            attempt_timeout_seconds=_positive_int(
                document.get("attempt_timeout_seconds"), "attempt_timeout_seconds", _MAX_SECONDS
            ),
            diagnosis_timeout_seconds=_positive_int(
                document.get("diagnosis_timeout_seconds"), "diagnosis_timeout_seconds", _MAX_SECONDS
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


class CampaignLauncher:
    """Run a bounded attempt/diagnosis campaign without target-specific policy."""

    def __init__(
        self,
        workflow: FactoryWorkflow,
        *,
        validator_identity: str,
        validator_key_path: str | Path,
        config: CampaignLaunchConfig,
    ) -> None:
        self.workflow = workflow
        self.validator_identity = validator_identity
        self.validator_key_path = Path(validator_key_path)
        self.config = config

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
                validator_diagnose=lambda outcome, **kwargs: self._diagnose(
                    outcome,
                    mode="diagnose",
                    **kwargs,
                ),
                validator_escalate=(
                    (lambda outcome, **kwargs: self._diagnose(outcome, mode="escalate", **kwargs))
                    if self.config.escalate_command is not None
                    else None
                ),
                validator_repair_launch_failure=(
                    self._repair_launch_failure
                    if self.config.validator_launch_repair_command is not None
                    else None
                ),
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
        completed = self._execute(
            self.config.attempt_command,
            timeout=self.config.attempt_timeout_seconds,
            environment={
                "FACTORY_CAMPAIGN_ATTEMPT_ID": attempt_id,
                "FACTORY_CAMPAIGN_REPAIR_BRIEF": str(repair_brief_path or ""),
            },
            label="attempt command",
        )
        projection = self.workflow.store.load(self._run_id_from_repair_brief(repair_brief_path))
        run_id = projection.run_id
        digests = self.workflow.store.current_artifact_digests(run_id)
        candidate_digest = str(digests.get("candidate", ""))
        tests_digest = str(digests.get("acceptance-tests", ""))
        if projection.state == RunState.PREVIEW:
            if completed.returncode != 0:
                raise RepairCampaignBlocked(
                    f"attempt command exited {completed.returncode} after preview",
                    validator_retriable=True,
                )
            return CampaignAttemptOutcome(
                attempt_id=attempt_id,
                candidate_digest=candidate_digest,
                tests_digest=tests_digest,
                projection=projection,
                _passed=True,
            )
        if projection.state == RunState.BLOCKED and candidate_digest and tests_digest:
            return CampaignAttemptOutcome(
                attempt_id=attempt_id,
                candidate_digest=candidate_digest,
                tests_digest=tests_digest,
                projection=projection,
                _passed=False,
            )
        state = projection.state
        if completed.returncode != 0:
            raise RepairCampaignBlocked(
                "attempt command exited "
                f"{completed.returncode} without a terminal candidate result ({state})",
                validator_retriable=True,
            )
        raise RepairCampaignBlocked(
            f"attempt command returned without a terminal candidate result ({state})",
            validator_retriable=True,
        )

    def _run_id_from_repair_brief(self, repair_brief_path: Path | None) -> str:
        """Use the only run this launcher was constructed to drive.

        The method is kept tiny so all subprocess boundaries share a single
        explicit run identity; a repair brief is passed through untouched and
        verified by :class:`RepairSupervisor` before the author lane receives it.
        """

        del repair_brief_path
        # The config is intentionally run-agnostic and reusable. ``run`` records this after
        # start so only the launcher, never an external command's environment, chooses it.
        if not hasattr(self, "_active_run_id"):
            raise CampaignLaunchError("campaign launcher has no active run")
        return self._active_run_id

    def _diagnose(
        self,
        outcome: CampaignAttemptOutcome,
        *,
        predecessor_ledger_head: str,
        phase_artifact_digests: Mapping[str, str],
        mode: str,
    ) -> RepairPlan:
        command = (
            self.config.diagnose_command if mode == "diagnose" else self.config.escalate_command
        )
        if command is None:
            raise RepairCampaignBlocked(f"no {mode} command is configured")
        failed_attempt_id = outcome.attempt_id
        output = self._diagnosis_path(
            outcome.projection.run_id,
            outcome.projection.generation,
            failed_attempt_id,
            mode,
        )
        if not output.exists():
            completed = self._execute(
                command,
                timeout=self.config.diagnosis_timeout_seconds,
                environment={
                    "FACTORY_CAMPAIGN_DIAGNOSIS_MODE": mode,
                    "FACTORY_CAMPAIGN_FAILED_ATTEMPT_ID": failed_attempt_id,
                    "FACTORY_CAMPAIGN_PLAN_OUTPUT": str(output),
                    "FACTORY_CAMPAIGN_PREDECESSOR_LEDGER_HEAD": predecessor_ledger_head,
                    "FACTORY_CAMPAIGN_PHASE_ARTIFACT_DIGESTS": json.dumps(
                        dict(phase_artifact_digests), sort_keys=True, separators=(",", ":")
                    ),
                },
                label=f"{mode} command",
            )
            if completed.returncode != 0:
                raise RepairCampaignBlocked(
                    f"{mode} command exited {completed.returncode}",
                    validator_retriable=True,
                )
        return _repair_plan(output)

    def _repair_launch_failure(self, attempt_id: str, reason: str) -> bool:
        command = self.config.validator_launch_repair_command
        if command is None:
            return False
        completed = self._execute(
            command,
            timeout=self.config.diagnosis_timeout_seconds,
            environment={
                "FACTORY_CAMPAIGN_FAILED_ATTEMPT_ID": attempt_id,
                "FACTORY_CAMPAIGN_LAUNCH_FAILURE_CLASS": _launch_failure_class(reason),
            },
            label="launch repair command",
        )
        return completed.returncode == 0

    def _diagnosis_path(
        self,
        run_id: str,
        generation: int,
        failed_attempt_id: str,
        mode: str,
    ) -> Path:
        return (
            self.workflow.root
            / run_id
            / "evidence"
            / "campaign-launcher"
            / "diagnoses"
            / f"generation-{generation}-{failed_attempt_id}-{mode}.json"
        )

    def _execute(
        self,
        command: Sequence[str],
        *,
        timeout: int,
        environment: Mapping[str, str],
        label: str,
    ) -> subprocess.CompletedProcess[bytes]:
        env = dict(os.environ)
        env.update(
            {
                "FACTORY_CAMPAIGN_RUN_ID": self._run_id_from_repair_brief(None),
                "FACTORY_CAMPAIGN_CONFIG_DIGEST": self.config.source_digest,
                **environment,
            }
        )
        try:
            return subprocess.run(
                command,
                cwd=self.config.workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RepairCampaignBlocked(
                f"{label} could not complete: {type(exc).__name__}",
                validator_retriable=True,
            ) from exc


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


def _workdir(value: object) -> Path:
    if not isinstance(value, str):
        raise CampaignLaunchError("campaign config workdir must be an absolute directory")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise CampaignLaunchError("campaign config workdir must be an absolute directory")
    return path.resolve(strict=True)


def _command(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > _MAX_COMMAND_ITEMS:
        raise CampaignLaunchError(f"campaign config {field} must be a bounded non-empty argv array")
    command = tuple(value)
    if not all(
        isinstance(item, str)
        and item
        and "\x00" not in item
        and len(item.encode("utf-8")) <= _MAX_COMMAND_ITEM_BYTES
        for item in command
    ):
        raise CampaignLaunchError(f"campaign config {field} has an invalid argv item")
    return command


def _optional_command(value: object, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _command(value, field)


def _positive_int(value: object, field: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise CampaignLaunchError(f"campaign config {field} must be between 1 and {maximum}")
    return value


def _repair_plan(path: Path) -> RepairPlan:
    raw = _read_regular_bounded(path, label="Validator repair plan", maximum=_MAX_PLAN_BYTES)
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RepairCampaignBlocked("Validator repair plan is not valid JSON") from exc
    if not isinstance(document, Mapping) or set(document) != {
        "summary",
        "actions",
        "intent_backreferences",
        "failure_signature",
    }:
        raise RepairCampaignBlocked("Validator repair plan has the wrong schema")
    references = document["intent_backreferences"]
    actions = document["actions"]
    if (
        not isinstance(document["summary"], str)
        or not isinstance(document["failure_signature"], str)
        or not isinstance(actions, list)
        or not all(isinstance(action, str) for action in actions)
        or not isinstance(references, list)
        or not all(isinstance(reference, Mapping) for reference in references)
    ):
        raise RepairCampaignBlocked("Validator repair plan intent_backreferences is invalid")
    try:
        return RepairPlan(
            summary=document["summary"],
            actions=tuple(actions),
            intent_backreferences=tuple(
                IntentBackreference.from_dict(reference)
                for reference in references
            ),
            failure_signature=document["failure_signature"],
        )
    except (TypeError, ValueError) as exc:
        raise RepairCampaignBlocked("Validator repair plan is invalid") from exc


def _launch_failure_class(reason: str) -> str:
    """Expose a stable class to a Validator repair command, never raw subprocess output."""

    if reason.startswith("attempt command exited"):
        return "attempt-command-no-terminal-result"
    if reason.startswith("attempt command returned"):
        return "attempt-command-no-terminal-result"
    if "TimeoutExpired" in reason:
        return "attempt-command-timeout"
    return "attempt-command-launch-failure"
