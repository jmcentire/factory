"""Human-authorized disposition for formerly passing tests that now fail.

The three phase artifacts remain the only authority for expected behavior. A newly ratified
artifact may state that behavior changed by exactly superseding the old intent item, but that
does not silently grant an agent permission to edit a guardrail. Updating an existing test also
requires a separately trusted, affirmative human ruling over the exact run, old and new behavior,
current phase versions, and either one assertion or an explicitly frozen test family.

The test-change authorization is therefore an impact disposition, not a fourth source of intent:
it can acknowledge the unique change already present in the phase artifacts; it cannot invent,
reinterpret, or invert expected behavior on its own.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from factory_core.manifest import digest_obj, verify_digest
from factory_core.provenance import REQUIRED_PHASES, IntentBackreference, ProvenanceBundle

TEST_ACTION_UPDATE = "update-test"
TEST_ACTION_FIX_IMPLEMENTATION = "fix-implementation"
TEST_ACTION_ROUTE_HUMAN = "route-human"
TEST_CHANGE_RULING = "change-expected-behavior"
TEST_CHANGE_AUTHORIZATION_SCHEMA_VERSION = "factory-test-change-authorization/1"

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class TestAssertionBinding:
    """One exact test assertion covered by a human ruling."""

    test_id: str
    assertion_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "test_id": self.test_id,
            "assertion_digest": self.assertion_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TestAssertionBinding:
        return cls(
            test_id=str(raw.get("test_id", "")),
            assertion_digest=str(raw.get("assertion_digest", "")),
        )


@dataclass(frozen=True)
class TestSelection:
    """A specific assertion or a named family whose membership is frozen in the ruling."""

    family_id: str = ""
    members: tuple[TestAssertionBinding, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "members": [
                member.to_dict()
                for member in sorted(
                    self.members,
                    key=lambda item: (item.test_id, item.assertion_digest),
                )
            ],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TestSelection:
        members = raw.get("members")
        return cls(
            family_id=str(raw.get("family_id", "")),
            members=tuple(
                TestAssertionBinding.from_dict(member)
                for member in (members if isinstance(members, list) else ())
                if isinstance(member, Mapping)
            ),
        )


@dataclass(frozen=True)
class TestChangeAuthorization:
    """Human ruling acknowledging the test impact of one artifact-authorized behavior change."""

    authorization_id: str
    version: str
    run_id: str
    human_authorizer: str
    ruling: str
    expected_change_statement: str
    phase_artifact_digests: Mapping[str, str] = field(default_factory=dict)
    old_behavior: IntentBackreference | None = None
    new_behavior: IntentBackreference | None = None
    selection: TestSelection = field(default_factory=TestSelection)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "phase_artifact_digests",
            MappingProxyType(dict(self.phase_artifact_digests)),
        )

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": TEST_CHANGE_AUTHORIZATION_SCHEMA_VERSION,
            "authorization_id": self.authorization_id,
            "version": self.version,
            "run_id": self.run_id,
            "human_authorizer": self.human_authorizer,
            "ruling": self.ruling,
            "expected_change_statement": self.expected_change_statement,
            "phase_artifact_digests": dict(sorted(self.phase_artifact_digests.items())),
            "old_behavior": self.old_behavior.to_dict() if self.old_behavior else None,
            "new_behavior": self.new_behavior.to_dict() if self.new_behavior else None,
            "selection": self.selection.to_dict(),
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TestChangeAuthorization:
        phase_raw = raw.get("phase_artifact_digests")
        old = raw.get("old_behavior")
        new = raw.get("new_behavior")
        selection = raw.get("selection")
        return cls(
            authorization_id=str(raw.get("authorization_id", "")),
            version=str(raw.get("version", "")),
            run_id=str(raw.get("run_id", "")),
            human_authorizer=str(raw.get("human_authorizer", "")),
            ruling=str(raw.get("ruling", "")),
            expected_change_statement=str(raw.get("expected_change_statement", "")),
            phase_artifact_digests=(
                {str(key): str(value) for key, value in phase_raw.items()}
                if isinstance(phase_raw, Mapping)
                else {}
            ),
            old_behavior=(IntentBackreference.from_dict(old) if isinstance(old, Mapping) else None),
            new_behavior=(IntentBackreference.from_dict(new) if isinstance(new, Mapping) else None),
            selection=(
                TestSelection.from_dict(selection)
                if isinstance(selection, Mapping)
                else TestSelection()
            ),
        )


@dataclass(frozen=True)
class ExistingTestFailure:
    """A test that passed on the trusted baseline and fails on the current candidate."""

    run_id: str
    test_id: str
    assertion_digest: str
    asserted_phase: str
    asserted_behavior: IntentBackreference | None
    passed_before: bool = True
    fails_now: bool = True


@dataclass(frozen=True)
class ExistingTestDisposition:
    """The required next action and the exact authority supporting it."""

    test_id: str
    action: str
    reason: str
    superseding_backreference: IntentBackreference | None = None
    current_backreference: IntentBackreference | None = None
    test_change_authorization_id: str = ""
    test_change_authorization_digest: str = ""

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
            "test_change_authorization_id": self.test_change_authorization_id,
            "test_change_authorization_digest": self.test_change_authorization_digest,
        }


def _authorization_issue(
    failure: ExistingTestFailure,
    provenance: ProvenanceBundle,
    superseder: IntentBackreference,
    authorization: TestChangeAuthorization | None,
    trusted_authorization_digest: str,
) -> str:
    if authorization is None:
        return "human-test-change-authorization-required"
    if not trusted_authorization_digest or not verify_digest(
        authorization.body(), trusted_authorization_digest
    ):
        return "test-change-authorization-untrusted"
    if not authorization.authorization_id.strip():
        return "test-change-authorization-id-missing"
    if not authorization.version.strip():
        return "test-change-authorization-version-missing"
    if authorization.run_id != failure.run_id or not failure.run_id.strip():
        return "test-change-authorization-run-mismatch"
    if not authorization.human_authorizer.strip():
        return "test-change-authorization-human-missing"
    if authorization.ruling != TEST_CHANGE_RULING:
        return "test-change-authorization-not-affirmative"
    if not authorization.expected_change_statement.strip():
        return "test-change-authorization-change-statement-missing"

    current_phases = {
        artifact.phase: artifact.content_digest
        for artifact in provenance.artifacts
        if artifact.phase in REQUIRED_PHASES
    }
    if dict(authorization.phase_artifact_digests) != current_phases:
        return "test-change-authorization-phase-artifacts-mismatch"
    if authorization.old_behavior != failure.asserted_behavior:
        return "test-change-authorization-old-behavior-mismatch"
    if authorization.new_behavior != superseder:
        return "test-change-authorization-new-behavior-mismatch"
    superseding_statements = [
        item.canonical_statement
        for artifact in provenance.artifacts
        if artifact.phase == failure.asserted_phase
        for item in artifact.items
        if artifact.backreference(item) == superseder
    ]
    if len(superseding_statements) != 1:
        return "test-change-authorization-new-behavior-unresolvable"
    if authorization.expected_change_statement != superseding_statements[0]:
        return "test-change-authorization-change-statement-mismatch"

    selection = authorization.selection
    if not selection.members:
        return "test-change-authorization-selection-empty"
    member_pairs = [(member.test_id, member.assertion_digest) for member in selection.members]
    if len(member_pairs) != len(set(member_pairs)):
        return "test-change-authorization-selection-duplicate"
    if not selection.family_id.strip() and len(selection.members) != 1:
        return "test-change-authorization-unnamed-family"
    if any(
        not member.test_id.strip() or not _SHA256_RE.fullmatch(member.assertion_digest)
        for member in selection.members
    ):
        return "test-change-authorization-selection-invalid"
    if (failure.test_id, failure.assertion_digest) not in member_pairs:
        return "test-change-authorization-does-not-cover-assertion"
    return ""


def dispose_existing_test_failure(
    failure: ExistingTestFailure,
    provenance: ProvenanceBundle,
    *,
    authorization: TestChangeAuthorization | None = None,
    trusted_authorization_digest: str = "",
) -> ExistingTestDisposition:
    """Classify one formerly passing failure without letting a ruling invent behavior."""

    test_id = failure.test_id.strip()
    if not test_id:
        return ExistingTestDisposition("", TEST_ACTION_ROUTE_HUMAN, "test-id-missing")
    if not failure.run_id.strip():
        return ExistingTestDisposition(test_id, TEST_ACTION_ROUTE_HUMAN, "run-id-missing")
    if failure.asserted_phase not in REQUIRED_PHASES:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            "asserted-phase-invalid",
        )
    if not _SHA256_RE.fullmatch(failure.assertion_digest):
        return ExistingTestDisposition(test_id, TEST_ACTION_ROUTE_HUMAN, "assertion-digest-invalid")
    if not failure.passed_before or not failure.fails_now:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            "not-a-formerly-passing-current-failure",
        )
    asserted = failure.asserted_behavior
    if asserted is None:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            "asserted-behavior-backreference-missing",
        )

    report = provenance.verify()
    if not report.satisfied:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            f"phase-artifact-provenance-invalid:{report.issues[0]}",
        )

    current: set[IntentBackreference] = set()
    retained_behavior: list[IntentBackreference] = []
    superseders: list[IntentBackreference] = []
    for artifact in provenance.artifacts:
        if artifact.phase != failure.asserted_phase:
            continue
        for item in artifact.items:
            current_reference = artifact.backreference(item)
            current.add(current_reference)
            if item.item_id == asserted.item_id and item.intent_digest == asserted.intent_digest:
                retained_behavior.append(current_reference)
            if asserted in item.supersedes:
                superseders.append(current_reference)

    unique_superseders = tuple(dict.fromkeys(superseders))
    unique_retained = tuple(dict.fromkeys(retained_behavior))
    if unique_superseders and unique_retained:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            "signed-artifacts-both-retain-and-supersede-asserted-behavior",
        )
    if len(unique_superseders) == 1:
        superseder = unique_superseders[0]
        issue = _authorization_issue(
            failure,
            provenance,
            superseder,
            authorization,
            trusted_authorization_digest,
        )
        if issue:
            return ExistingTestDisposition(
                test_id,
                TEST_ACTION_ROUTE_HUMAN,
                issue,
                superseding_backreference=superseder,
            )
        assert authorization is not None
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_UPDATE,
            "artifact-supersession-and-human-test-impact-ruling-agree",
            superseding_backreference=superseder,
            test_change_authorization_id=authorization.authorization_id,
            test_change_authorization_digest=authorization.content_digest,
        )
    if len(unique_superseders) > 1:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            "multiple-signed-items-supersede-asserted-behavior",
        )
    if asserted in current:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_FIX_IMPLEMENTATION,
            "signed-artifacts-retain-asserted-behavior",
            current_backreference=asserted,
        )
    if len(unique_retained) == 1:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_FIX_IMPLEMENTATION,
            "new-artifact-version-retains-exact-asserted-behavior",
            current_backreference=unique_retained[0],
        )
    if len(unique_retained) > 1:
        return ExistingTestDisposition(
            test_id,
            TEST_ACTION_ROUTE_HUMAN,
            "multiple-current-items-retain-asserted-behavior",
        )
    return ExistingTestDisposition(
        test_id,
        TEST_ACTION_ROUTE_HUMAN,
        "signed-artifacts-silent-on-asserted-behavior",
    )
