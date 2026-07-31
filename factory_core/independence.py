"""Graded oracle independence, per-agent verifier identity, and the structural-depth trade.

Independence is not a property a run either has or does not have. It is a five-tier scale, and
which tier a run reached changes what its verdict is worth: a moderate-tier verdict and a
stronger-tier verdict are not the same evidence, and nothing downstream can tell them apart
unless the tier is recorded.

This module makes that recordable and checkable:

* every agent that produced or judged the change records its model family, model version, and
  directive version — without those, the requalification-on-change rule cannot be applied after
  the fact, because no one can tell which verdicts predate a swap;
* the tier is **derived from the recorded arrangement** (shared context, open channel, model
  families, mechanical backing), never asserted, and a claim above the derived tier is an
  integrity failure rather than a weaker verdict; and
* the Tester's implementation-informed structural mode is recorded as the trade it is —
  permitted where a signed interface contract anchors the oracle, and otherwise forgone in
  favor of isolation, which moves branch-level depth onto the Validator as a mutation-check
  obligation.

Fail-closed by construction: unrecorded arrangement facts describe the *weakest* tier, because
an absent fact is not evidence of separation. The module resolves no identities and reads no
disk; the caller supplies the trusted human roster and the set of resolved backreferences.

All model names, versions, directive versions, and mechanism ids are opaque target data. The
only vocabulary fixed here is the doctrine's five tiers and two structural modes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factory_core.evidence import EvidenceIntegrity
from factory_core.provenance import IntentBackreference

INDEPENDENCE_WEAKEST = "weakest"
INDEPENDENCE_WEAK = "weak"
INDEPENDENCE_MODERATE = "moderate"
INDEPENDENCE_STRONGER = "stronger"
INDEPENDENCE_STRONGEST = "strongest"

# Ordered weakest to strongest. The order is the doctrine's; the rank is what lets a claim be
# compared against the derived value.
INDEPENDENCE_TIERS: tuple[str, ...] = (
    INDEPENDENCE_WEAKEST,
    INDEPENDENCE_WEAK,
    INDEPENDENCE_MODERATE,
    INDEPENDENCE_STRONGER,
    INDEPENDENCE_STRONGEST,
)

_TIER_RANK: Mapping[str, int] = {tier: rank for rank, tier in enumerate(INDEPENDENCE_TIERS)}

ROLE_CODER = "coder"
ROLE_TESTER = "tester"
ROLE_VALIDATOR = "validator"

# The three roles that produce or judge a change. Every one of them is recorded, because the
# manifest requirement is about verdict provenance, not only about the two isolated lanes.
RECORDED_ROLES: tuple[str, ...] = (ROLE_CODER, ROLE_TESTER, ROLE_VALIDATOR)

# The two lanes whose separation the tier describes.
ORACLE_LANE_ROLES: tuple[str, ...] = (ROLE_CODER, ROLE_TESTER)

STRUCTURAL_MODE_ISOLATED = "isolated"
STRUCTURAL_MODE_IMPLEMENTATION_INFORMED = "implementation-informed"

STRUCTURAL_MODES: tuple[str, ...] = (
    STRUCTURAL_MODE_ISOLATED,
    STRUCTURAL_MODE_IMPLEMENTATION_INFORMED,
)


def normalize_independence_label(value: str) -> str:
    """Normalize opaque target-supplied labels for deterministic matching."""

    return value.strip().casefold()


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    return (str(value),)


def tier_rank(tier: str) -> int:
    """Rank of a tier, or ``-1`` when the label is not one of the doctrine's five."""

    return _TIER_RANK.get(normalize_independence_label(tier), -1)


@dataclass(frozen=True)
class AgentIdentity:
    """One agent that produced or judged the change, with what it ran as and under."""

    role: str
    model_family: str = ""
    model_version: str = ""
    directive_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", normalize_independence_label(self.role))
        object.__setattr__(self, "model_family", normalize_independence_label(self.model_family))

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model_family": self.model_family,
            "model_version": self.model_version,
            "directive_version": self.directive_version,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AgentIdentity:
        return cls(
            role=str(raw.get("role", "")),
            model_family=str(raw.get("model_family", "")),
            model_version=str(raw.get("model_version", "")),
            directive_version=str(raw.get("directive_version", "")),
        )


@dataclass(frozen=True)
class StructuralModeRecord:
    """Which oracle-depth trade the Tester made, and the obligation it leaves behind.

    Reading the implementation to hunt branches, races, and transaction errors buys depth and
    opens a channel through which the implementation can move the oracle. It is therefore
    permitted only where a **signed interface contract** pins the expectations. Where the
    contract does not exist the correct trade is total isolation, and the branch-level depth
    that was not purchased becomes the Validator's mutation-check obligation plus an explicit
    note in the decision package — an unpurchased depth nobody recorded reads as depth.
    """

    mode: str = ""
    contract_backreference: IntentBackreference | None = None
    mutation_evidence: EvidenceIntegrity | None = None
    decision_package_note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_independence_label(self.mode))

    def authority_body(self) -> dict[str, Any]:
        """The exact fields mutation evidence for a forgone structural pass must bind."""

        return {
            "structural_mode": self.mode,
            "mutation_checked_critical_controls": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "contract_backreference": (
                self.contract_backreference.to_dict()
                if self.contract_backreference is not None
                else None
            ),
            "mutation_evidence": (
                self.mutation_evidence.to_dict() if self.mutation_evidence is not None else None
            ),
            "decision_package_note": self.decision_package_note,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StructuralModeRecord:
        reference_raw = raw.get("contract_backreference")
        evidence_raw = raw.get("mutation_evidence")
        return cls(
            mode=str(raw.get("mode", "")),
            contract_backreference=(
                IntentBackreference.from_dict(reference_raw)
                if isinstance(reference_raw, Mapping)
                else None
            ),
            mutation_evidence=EvidenceIntegrity.from_dict(
                evidence_raw if isinstance(evidence_raw, Mapping) else None
            ),
            decision_package_note=str(raw.get("decision_package_note", "")),
        )


@dataclass(frozen=True)
class IndependenceRecord:
    """The recorded arrangement a verdict was produced in.

    The three booleans default to the *worst* case on purpose. ``shared_context`` and
    ``channel_open`` default true and ``mechanism_ids`` defaults empty, so a caller that records
    nothing derives the weakest tier rather than inheriting an unearned one.
    """

    agents: tuple[AgentIdentity, ...] = ()
    shared_context: bool = True
    channel_open: bool = True
    mechanism_ids: tuple[str, ...] = ()
    claimed_tier: str = ""
    structural_mode: StructuralModeRecord | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "claimed_tier", normalize_independence_label(self.claimed_tier))
        object.__setattr__(
            self,
            "mechanism_ids",
            tuple(
                dict.fromkeys(
                    normalize_independence_label(item)
                    for item in self.mechanism_ids
                    if normalize_independence_label(item)
                )
            ),
        )

    def agent(self, role: str) -> AgentIdentity | None:
        wanted = normalize_independence_label(role)
        for identity in self.agents:
            if identity.role == wanted:
                return identity
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": [identity.to_dict() for identity in self.agents],
            "shared_context": self.shared_context,
            "channel_open": self.channel_open,
            "mechanism_ids": list(self.mechanism_ids),
            "claimed_tier": self.claimed_tier,
            "structural_mode": (
                self.structural_mode.to_dict() if self.structural_mode is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> IndependenceRecord:
        raw_agents = raw.get("agents")
        structural_raw = raw.get("structural_mode")
        return cls(
            agents=tuple(
                AgentIdentity.from_dict(item)
                for item in (raw_agents if isinstance(raw_agents, Sequence) else ())
                if isinstance(item, Mapping)
            ),
            shared_context=bool(raw.get("shared_context", True)),
            channel_open=bool(raw.get("channel_open", True)),
            mechanism_ids=_as_str_tuple(raw.get("mechanism_ids")),
            claimed_tier=str(raw.get("claimed_tier", "")),
            structural_mode=(
                StructuralModeRecord.from_dict(structural_raw)
                if isinstance(structural_raw, Mapping)
                else None
            ),
        )


@dataclass(frozen=True)
class IndependenceReport:
    """Derived tier plus class-disposable findings.

    ``gaps`` are absences a caller's criticality policy disposes. ``failures`` are observed
    negative arrangements. ``integrity_issues`` are malformed, duplicated, or overclaimed
    records. Neither failures nor integrity issues convert into a waiver.
    """

    derived_tier: str
    claimed_tier: str
    gaps: tuple[str, ...]
    failures: tuple[str, ...]
    integrity_issues: tuple[str, ...]
    reports: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return not self.gaps and not self.failures and not self.integrity_issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "derived_tier": self.derived_tier,
            "claimed_tier": self.claimed_tier,
            "gaps": list(self.gaps),
            "failures": list(self.failures),
            "integrity_issues": list(self.integrity_issues),
            "reports": list(self.reports),
        }


def derive_independence_tier(record: IndependenceRecord) -> str:
    """Derive the tier from the recorded arrangement alone.

    The ladder, weakest to strongest: shared context is worth almost nothing; separate context
    over an open channel defends only against careless error; closing the channel defends
    against tuning to the oracle; different model families stop *guaranteeing* a shared frame;
    and a reproducible mechanism has no frame to share. A mechanism is recorded by id rather
    than by a boolean, because "we had a mechanism" is exactly the claim that needs a referent.
    """

    if record.mechanism_ids:
        return INDEPENDENCE_STRONGEST
    if record.shared_context:
        return INDEPENDENCE_WEAKEST
    if record.channel_open:
        return INDEPENDENCE_WEAK

    families = {
        identity.model_family
        for identity in (record.agent(role) for role in ORACLE_LANE_ROLES)
        if identity is not None and identity.model_family
    }
    if len(families) < len(ORACLE_LANE_ROLES):
        # An unrecorded family cannot demonstrate diversity, so the arrangement is at most the
        # same-model tier.
        return INDEPENDENCE_MODERATE
    return INDEPENDENCE_STRONGER if len(families) > 1 else INDEPENDENCE_MODERATE


def verify_independence(
    record: IndependenceRecord | None,
    *,
    resolved_backreferences: Iterable[IntentBackreference] = (),
    authority_available: bool = True,
) -> IndependenceReport:
    """Verify verifier identity, the tier claim, and the structural-depth trade.

    ``resolved_backreferences`` are the references a provenance verifier already resolved
    against trusted phase artifacts. Structural mode claims a signed interface contract; this
    function requires that contract reference to be one of them, so "a contract exists" cannot
    be self-asserted here.

    No identity in this record is an authority — these are recorded facts about what ran — so no
    segregation policy is consulted.
    """

    gaps: list[str] = []
    failures: list[str] = []
    integrity: list[str] = []
    reports: list[str] = []

    if record is None:
        return IndependenceReport(
            derived_tier=INDEPENDENCE_WEAKEST,
            claimed_tier="",
            gaps=("independence-record-missing",),
            failures=(),
            integrity_issues=(),
            reports=(),
        )

    seen_roles: set[str] = set()
    for identity in record.agents:
        if not identity.role:
            integrity.append("independence-agent-role-missing")
            continue
        if identity.role in seen_roles:
            integrity.append(f"independence-agent-duplicate:{identity.role}")
            continue
        seen_roles.add(identity.role)
        if identity.role not in RECORDED_ROLES:
            reports.append(f"independence-agent-outside-roles:{identity.role}")

    for role in RECORDED_ROLES:
        recorded = record.agent(role)
        if recorded is None:
            gaps.append(f"independence-agent-missing:{role}")
            continue
        if not recorded.model_family:
            gaps.append(f"independence-model-family-unrecorded:{role}")
        if not recorded.model_version.strip():
            gaps.append(f"independence-model-version-unrecorded:{role}")
        if not recorded.directive_version.strip():
            gaps.append(f"independence-directive-version-unrecorded:{role}")

    derived = derive_independence_tier(record)
    if not record.claimed_tier:
        gaps.append("independence-tier-unclaimed")
    elif tier_rank(record.claimed_tier) < 0:
        integrity.append(f"independence-tier-unknown:{record.claimed_tier}")
    elif tier_rank(record.claimed_tier) > tier_rank(derived):
        # Not a weaker verdict — a false one. The arrangement on record cannot produce the
        # independence being claimed for it.
        integrity.append(f"independence-tier-overclaimed:{record.claimed_tier}:{derived}")
    elif tier_rank(record.claimed_tier) < tier_rank(derived):
        reports.append(f"independence-tier-understated:{record.claimed_tier}:{derived}")

    if record.channel_open:
        # The whole independence mechanism is structural: two agents with no channel, not two
        # agents agreeing not to peek.
        failures.append("independence-coder-tester-channel-open")
    if record.shared_context:
        reports.append("independence-shared-context-recorded")

    _verify_structural_mode(
        record.structural_mode,
        frozenset(resolved_backreferences),
        authority_available,
        gaps,
        integrity,
        reports,
    )

    return IndependenceReport(
        derived_tier=derived,
        claimed_tier=record.claimed_tier,
        gaps=tuple(dict.fromkeys(gaps)),
        failures=tuple(dict.fromkeys(failures)),
        integrity_issues=tuple(dict.fromkeys(integrity)),
        reports=tuple(dict.fromkeys(reports)),
    )


def _verify_structural_mode(
    structural: StructuralModeRecord | None,
    resolved: frozenset[IntentBackreference],
    authority_available: bool,
    gaps: list[str],
    integrity: list[str],
    reports: list[str],
) -> None:
    if structural is None or not structural.mode:
        gaps.append("structural-mode-unrecorded")
        return
    if structural.mode not in STRUCTURAL_MODES:
        integrity.append(f"structural-mode-unknown:{structural.mode}")
        return

    if structural.mode == STRUCTURAL_MODE_IMPLEMENTATION_INFORMED:
        reference = structural.contract_backreference
        if reference is None:
            integrity.append("structural-mode-without-signed-contract")
        elif reference in resolved:
            reports.append("structural-depth-purchased-against-signed-contract")
        elif not authority_available:
            gaps.append("structural-mode-contract-authority-unavailable")
        else:
            # An oracle that read the implementation with nothing pinning its expectations is
            # not independent evidence, so this is an integrity failure rather than an absence.
            integrity.append("structural-mode-contract-unresolved")
        return

    evidence = structural.mutation_evidence
    if evidence is None or not evidence.present:
        gaps.append("structural-depth-mutation-evidence-missing")
    elif not evidence.verify():
        integrity.append("structural-depth-mutation-evidence-digest-mismatch")
    elif not evidence.verifies_binding(structural.authority_body()):
        integrity.append("structural-depth-mutation-evidence-subject-mismatch")
    if not structural.decision_package_note.strip():
        gaps.append("structural-depth-note-missing")
    else:
        reports.append("structural-depth-not-purchased")
