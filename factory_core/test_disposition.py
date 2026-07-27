"""Authorization-based disposition for formerly passing tests that now fail.

Only the Validator receives both the frozen phase artifacts and execution results. This module
does not run tests or edit either side; it gives that role a deterministic classification:

* an exact signed supersession authorizes updating the test;
* an assertion still present unchanged means the implementation regressed; and
* silence or conflicting supersessions route to the human.

The control is authorization, not test immutability. A test is never updated merely because it
is inconvenient, and a new signed artifact version cannot inherit a prior version's authority
through a stable item id because backreferences bind the whole artifact digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factory_core.provenance import IntentBackreference, ProvenanceBundle

TEST_ACTION_UPDATE = "update-test"
TEST_ACTION_FIX_IMPLEMENTATION = "fix-implementation"
TEST_ACTION_ROUTE_HUMAN = "route-human"


@dataclass(frozen=True)
class ExistingTestFailure:
    """A test that passed on the trusted baseline and fails on the current candidate."""

    test_id: str
    asserted_behavior: IntentBackreference | None
    passed_before: bool = True
    fails_now: bool = True


@dataclass(frozen=True)
class ExistingTestDisposition:
    """The required next action and the authority supporting it."""

    test_id: str
    action: str
    reason: str
    superseding_backreference: IntentBackreference | None = None
    current_backreference: IntentBackreference | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "action": self.action,
            "reason": self.reason,
            "superseding_backreference": (
                self.superseding_backreference.to_dict()
                if self.superseding_backreference is not None
                else None
            ),
            "current_backreference": (
                self.current_backreference.to_dict()
                if self.current_backreference is not None
                else None
            ),
        }


def dispose_existing_test_failure(
    failure: ExistingTestFailure,
    provenance: ProvenanceBundle,
) -> ExistingTestDisposition:
    """Classify one formerly passing failure against the current signed artifacts.

    Invalid provenance cannot safely justify either a code or test edit, so it routes to the
    human with the verifier's defect. Multiple current items superseding the same old
    authority are likewise ambiguous rather than selected by ordering.
    """

    test_id = failure.test_id.strip()
    if not test_id:
        return ExistingTestDisposition(
            test_id="",
            action=TEST_ACTION_ROUTE_HUMAN,
            reason="test-id-missing",
        )
    if not failure.passed_before or not failure.fails_now:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_ROUTE_HUMAN,
            reason="not-a-formerly-passing-current-failure",
        )
    asserted = failure.asserted_behavior
    if asserted is None:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_ROUTE_HUMAN,
            reason="asserted-behavior-backreference-missing",
        )

    report = provenance.verify()
    if not report.satisfied:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_ROUTE_HUMAN,
            reason=f"phase-artifact-provenance-invalid:{report.issues[0]}",
        )

    current: set[IntentBackreference] = set()
    retained_behavior: list[IntentBackreference] = []
    superseders: list[IntentBackreference] = []
    for artifact in provenance.artifacts:
        for item in artifact.items:
            current_reference = artifact.backreference(item)
            current.add(current_reference)
            if (
                item.item_id == asserted.item_id
                and item.intent_digest == asserted.intent_digest
            ):
                retained_behavior.append(current_reference)
            if asserted in item.supersedes:
                superseders.append(current_reference)

    unique_superseders = tuple(dict.fromkeys(superseders))
    unique_retained = tuple(dict.fromkeys(retained_behavior))
    if unique_superseders and unique_retained:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_ROUTE_HUMAN,
            reason="signed-artifacts-both-retain-and-supersede-asserted-behavior",
        )
    if len(unique_superseders) == 1:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_UPDATE,
            reason="signed-artifact-supersedes-asserted-behavior",
            superseding_backreference=unique_superseders[0],
        )
    if len(unique_superseders) > 1:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_ROUTE_HUMAN,
            reason="multiple-signed-items-supersede-asserted-behavior",
        )
    if asserted in current:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_FIX_IMPLEMENTATION,
            reason="signed-artifacts-retain-asserted-behavior",
            current_backreference=asserted,
        )
    if len(unique_retained) == 1:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_FIX_IMPLEMENTATION,
            reason="new-artifact-version-retains-exact-asserted-behavior",
            current_backreference=unique_retained[0],
        )
    if len(unique_retained) > 1:
        return ExistingTestDisposition(
            test_id=test_id,
            action=TEST_ACTION_ROUTE_HUMAN,
            reason="multiple-current-items-retain-asserted-behavior",
        )
    return ExistingTestDisposition(
        test_id=test_id,
        action=TEST_ACTION_ROUTE_HUMAN,
        reason="signed-artifacts-silent-on-asserted-behavior",
    )
