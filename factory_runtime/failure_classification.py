"""Validator-only classification of terminal attempt failures.

Raw lane output is useful evidence for the Validator but is never a Coder
input.  This module deliberately emits only a small, stable capsule: an owner,
code, and safe summary.  Callers retain the raw logs at their role-local paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from factory_runtime.runner_termination import IDLE_LIMIT, OUTPUT_LIMIT, WALL_LIMIT


@dataclass(frozen=True)
class FailureCapsule:
    """A durable, non-oracle-bearing disposition for one failed attempt."""

    owner: str
    code: str
    summary: str

    def document(self) -> dict[str, str]:
        return {
            "schema_version": "factory-failure-capsule/1",
            "owner": self.owner,
            "code": self.code,
            "summary": self.summary,
        }


def classify_terminal_failure(
    *,
    final: Mapping[str, object] | None,
    caller_returncode: int,
    caller_stdout: str,
    caller_stderr: str,
    validator_result_present: bool,
    coder_receipt_present: bool,
    tester_receipt_present: bool,
    invocation_termination_reason: str | None = None,
    acceptance_lifecycle_required: bool = False,
    acceptance_phase: str | None = None,
) -> FailureCapsule:
    """Classify an attempt without serialising raw lane or oracle output.

    Textual process output is deliberately not interpreted as authority.  It is
    untrusted model-controlled evidence retained for the Validator only; stable
    ownership is derived from authenticated runtime state and the supervisor's
    closed termination vocabulary.
    """

    _ = caller_stdout, caller_stderr
    if invocation_termination_reason in {WALL_LIMIT, IDLE_LIMIT}:
        return FailureCapsule(
            owner="validator-harness",
            code="runner-invocation-timeout",
            summary="The Validator-owned runner exceeded its declared invocation time limit.",
        )
    if invocation_termination_reason == OUTPUT_LIMIT:
        return FailureCapsule(
            owner="validator-harness",
            code="runner-invocation-output-limit",
            summary="The Validator-owned runner exceeded its declared output limit.",
        )

    final = final or {}
    status = str(final.get("status", ""))
    if not final:
        return FailureCapsule(
            owner="validator-harness",
            code="caller-missing-terminal-report",
            summary="The Validator caller ended without writing a terminal report.",
        )
    if status == "runtime-exception":
        return FailureCapsule(
            owner="validator-harness",
            code="validator-caller-exception",
            summary="The Validator caller raised before it could complete the attempt protocol.",
        )
    if not validator_result_present:
        return FailureCapsule(
            owner="validator-harness",
            code="validator-acceptance-not-recorded",
            summary="Author outputs were not followed by a recorded Validator acceptance result.",
        )
    if not tester_receipt_present:
        return FailureCapsule(
            owner="tester",
            code="tester-receipt-missing",
            summary="Tester did not produce a valid completed receipt for this attempt.",
        )
    if not coder_receipt_present:
        return FailureCapsule(
            owner="coder",
            code="coder-receipt-missing",
            summary="Coder did not produce a valid completed receipt for this attempt.",
        )
    if acceptance_lifecycle_required and acceptance_phase not in {
        "behavior-started",
        "behavior-complete",
    }:
        return FailureCapsule(
            owner="validator-harness",
            code="acceptance-setup-not-reached",
            summary=(
                "The Validator-owned acceptance topology did not reach a recorded "
                "behavioral phase."
            ),
        )
    if caller_returncode != 0:
        return FailureCapsule(
            owner="validator-harness",
            code="validator-caller-nonzero-exit",
            summary=(
                "The Validator caller ended nonzero after recording an incomplete "
                "terminal result."
            ),
        )
    return FailureCapsule(
        owner="coder",
        code="candidate-failed-acceptance",
        summary=(
            "The sealed oracle completed and the candidate did not satisfy "
            "Validator acceptance."
        ),
    )
