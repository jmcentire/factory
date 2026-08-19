"""Validator-only classification of terminal attempt failures.

Raw lane output is useful evidence for the Validator but is never a Coder
input.  This module deliberately emits only a small, stable capsule: an owner,
code, and safe summary.  Callers retain the raw logs at their role-local paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


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
) -> FailureCapsule:
    """Classify an attempt without serialising raw lane or oracle output.

    The textual inputs are inspected only in Validator memory.  No token from
    them is copied into the resulting capsule.  This keeps test mechanics and
    command output out of all downstream repair briefs.
    """

    if invocation_termination_reason in {"wall-timeout", "idle-timeout"}:
        return FailureCapsule(
            owner="validator-harness",
            code="runner-invocation-timeout",
            summary="The Validator-owned runner exceeded its declared invocation time limit.",
        )
    if invocation_termination_reason == "output-limit":
        return FailureCapsule(
            owner="validator-harness",
            code="runner-invocation-output-limit",
            summary="The Validator-owned runner exceeded its declared output limit.",
        )

    final = final or {}
    status = str(final.get("status", ""))
    evidence = "\n".join((caller_stdout, caller_stderr, str(final.get("exception", "")))).lower()

    if not final:
        return FailureCapsule(
            owner="validator-harness",
            code="caller-missing-terminal-report",
            summary="The Validator caller ended without writing a terminal report.",
        )
    if status == "runtime-exception":
        if "direnv" in evidence or "coding_agent" in evidence or "coding-agent" in evidence:
            return FailureCapsule(
                owner="host-prerequisite",
                code="validator-launch-environment-unavailable",
                summary="The Validator could not establish its declared launch environment.",
            )
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
