"""factory_core.promotion — the fail-closed, default-deny promotion (merge-gate) decision.

This is the generic extraction of the "may this built artifact be promoted / merged?" gate
proven on the first consuming target. That target's implementation names its own gate ids, its
own risk tiers, and its own action categories, and reads its own content-addressed manifest.
Here, none of that vocabulary is present: the tiers, the categories, the required gate ids, and
the approver thresholds are all **data** the target supplies through :class:`ConsequenceProfile`
and :class:`PromotionRequest`. This core owns only the neutral decision.

Relationship to :mod:`factory_core.manifest`: the manifest module owns *write-time* segregation
of duties for a single ledger entry (implementer/verifier/approver distinctness, approver-is-
human) and the tamper-evident hash-chain. This module does NOT duplicate that — it *composes*
it: it reuses :class:`~factory_core.manifest.SegregationPolicy` for DENY-wins identity
resolution and :func:`~factory_core.manifest.verify_digest` for the content-address tamper
check, and adds the *aggregate* promotion decision on top: gate-outcome quorum, the
consequence-driven distinct-human approver floor, and default-deny composition of every
condition into one verdict.

The doctrine this implements (the seven non-negotiables; oracle-adequacy gating):

  * **Default-deny.** ``allowed`` is true only when EVERY condition is affirmatively satisfied.
    Any missing input, tamper, failed/absent gate, or SoD violation denies. The reason set is
    the falsifiable evidence of *why* it denied.
  * **An agent can never approve** (fail closed on authorization). Approver resolution is
    DENY-wins (the agent denylist is checked before the human allowlist), inherited from the
    :class:`SegregationPolicy`. No policy ⇒ no identity resolves ⇒ deny.
  * **The consequential floor.** A consequential change (a data-declared risk tier or action
    category) requires at least :data:`CONSEQUENTIAL_APPROVER_FLOOR` (= two) DISTINCT enrolled
    humans — a floor the core enforces and a target profile may raise but never lower. A
    non-consequential change still requires at least one enrolled human (the mechanical gates
    are the second reviewer, but a human still accepts the risk).

Posture (matching the sibling modules): stdlib only, side-effect free at import, no clock, no
disk-reading, no target contact. Every per-target specific (which tiers/categories are
consequential, which gates are required, the exact thresholds) arrives as data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from factory_core.manifest import SegregationPolicy, verify_digest

# --------------------------------------------------------------------------- #
# Doctrine constant: the non-lowerable consequential-approver floor
# --------------------------------------------------------------------------- #

#: A consequential change requires at least this many DISTINCT enrolled humans. This is a
#: property of the factory doctrine (a consequential action is never taken on one authority),
#: NOT of any target — so it lives in the core and a target profile may raise it but never lower
#: it. A non-consequential change requires at least one (see :data:`BASELINE_APPROVER_FLOOR`).
CONSEQUENTIAL_APPROVER_FLOOR = 2

#: The lowest an approver requirement can ever be: at least one enrolled human accepts the risk,
#: even for the most inert change. (An agent can never fill this slot — DENY-wins.)
BASELINE_APPROVER_FLOOR = 1


class PromotionError(ValueError):
    """Raised when a promotion input is structurally invalid (fail closed)."""


# --------------------------------------------------------------------------- #
# Gate outcomes (the mechanical, reproducible checks that stand in for review)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class GateOutcome:
    """One mechanical gate's captured outcome. ``id`` is the target's opaque gate name; the core
    assigns it no meaning beyond matching it against the profile's required set. ``detail`` is
    free-form evidence for the outcome (e.g. a test count, a build log pointer)."""

    id: str
    passed: bool
    detail: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> GateOutcome:
        return cls(
            id=str(raw.get("id", "")).strip(),
            passed=bool(raw.get("passed", False)),
            detail=str(raw.get("detail", "")),
        )


# --------------------------------------------------------------------------- #
# The evidence-integrity precondition (content-addressed tamper check)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EvidenceIntegrity:
    """The content-addressed artifact this promotion is bound to, plus its claimed address.

    A promotion is bound to a specific verified artifact (the target's evidence manifest, a spec
    digest bundle, whatever it content-addresses). ``body`` is that artifact as a canonical-
    JSON-serializable mapping; ``claimed_digest`` is the address carried alongside it (e.g. from
    a signed side-record). The gate recomputes the address and constant-time-compares — any
    field mutation moves the address and denies. An absent body or digest denies (fail closed:
    an unverifiable artifact is never promoted).
    """

    body: Mapping[str, Any] | None = None
    claimed_digest: str = ""

    @property
    def present(self) -> bool:
        return self.body is not None and bool(self.claimed_digest)

    def verify(self) -> bool:
        """Constant-time content-address check via :func:`factory_core.manifest.verify_digest`."""
        if self.body is None or not self.claimed_digest:
            return False
        return verify_digest(dict(self.body), self.claimed_digest)


# --------------------------------------------------------------------------- #
# The consequence profile (TARGET DATA: what is consequential + the thresholds)
# --------------------------------------------------------------------------- #

def _norm(value: str) -> str:
    """Casefold + trim a tier/category/gate label so matching is whitespace/case-insensitive.
    Neutral string hygiene — the LABELS themselves are target data, never named here."""
    return value.strip().casefold()


def _norm_set(values: Iterable[str]) -> frozenset[str]:
    return frozenset(_norm(v) for v in values if v and v.strip())


@dataclass(frozen=True)
class ConsequenceProfile:
    """The target's promotion policy, as DATA. The core names none of these labels.

    * ``consequential_tiers`` — the risk-tier labels that make a change consequential;
    * ``consequential_categories`` — the action-category labels that make a change consequential
      regardless of tier;
    * ``required_gate_ids`` — the mechanical gates that must all be present AND passed;
    * ``baseline_min_approvers`` — distinct-human approvals a non-consequential change needs
      (clamped up to :data:`BASELINE_APPROVER_FLOOR`);
    * ``consequential_min_approvers`` — distinct-human approvals a consequential change needs
      (clamped up to :data:`CONSEQUENTIAL_APPROVER_FLOOR` — the target may raise the floor,
      never lower it).

    Labels are stored normalized (casefold+trim) so a target's data and a request's values
    compare regardless of case/whitespace.
    """

    consequential_tiers: frozenset[str] = frozenset()
    consequential_categories: frozenset[str] = frozenset()
    required_gate_ids: frozenset[str] = frozenset()
    baseline_min_approvers: int = BASELINE_APPROVER_FLOOR
    consequential_min_approvers: int = CONSEQUENTIAL_APPROVER_FLOOR

    def __post_init__(self) -> None:
        object.__setattr__(self, "consequential_tiers", _norm_set(self.consequential_tiers))
        object.__setattr__(
            self, "consequential_categories", _norm_set(self.consequential_categories)
        )
        object.__setattr__(self, "required_gate_ids", _norm_set(self.required_gate_ids))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ConsequenceProfile:
        def _ints(key: str, default: int) -> int:
            val = raw.get(key, default)
            try:
                return int(val)
            except (TypeError, ValueError) as exc:
                raise PromotionError(f"{key!r} must be an integer, got {val!r}") from exc

        return cls(
            consequential_tiers=frozenset(_as_str_tuple(raw.get("consequential_tiers"))),
            consequential_categories=frozenset(
                _as_str_tuple(raw.get("consequential_categories"))
            ),
            required_gate_ids=frozenset(_as_str_tuple(raw.get("required_gate_ids"))),
            baseline_min_approvers=_ints("baseline_min_approvers", BASELINE_APPROVER_FLOOR),
            consequential_min_approvers=_ints(
                "consequential_min_approvers", CONSEQUENTIAL_APPROVER_FLOOR
            ),
        )

    def is_consequential(self, tier: str, categories: Iterable[str]) -> bool:
        """A change is consequential if its tier is a consequential tier OR any of its declared
        categories is a consequential category. Comparison is on normalized labels."""
        if _norm(tier) in self.consequential_tiers:
            return True
        return bool(_norm_set(categories) & self.consequential_categories)

    def required_approvers(self, tier: str, categories: Iterable[str]) -> int:
        """The distinct-human approver requirement, with the doctrine floors applied. A
        consequential change is floored at :data:`CONSEQUENTIAL_APPROVER_FLOOR`; every change is
        floored at :data:`BASELINE_APPROVER_FLOOR` (the target may raise, never lower)."""
        if self.is_consequential(tier, categories):
            return max(CONSEQUENTIAL_APPROVER_FLOOR, self.consequential_min_approvers)
        return max(BASELINE_APPROVER_FLOOR, self.baseline_min_approvers)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(v) for v in value)
    return (str(value),)


# --------------------------------------------------------------------------- #
# The promotion request (the specific artifact being promoted)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PromotionRequest:
    """Everything about the specific change being promoted. All values are data.

    ``tier`` / ``categories`` classify consequence (matched against the profile). ``gates`` are
    the captured mechanical outcomes. ``implementer`` built it, ``verifier`` proved it (both may
    be agents — the factory implements/verifies mechanically). ``approvers`` are the identities
    that approved THIS artifact; each is resolved DENY-wins against the roster policy, and a
    human with several aliases still counts once. ``evidence`` is the content-addressed tamper
    precondition (absent ⇒ deny).
    """

    tier: str = ""
    categories: tuple[str, ...] = ()
    gates: tuple[GateOutcome, ...] = ()
    implementer: str = ""
    verifier: str = ""
    approvers: tuple[str, ...] = ()
    evidence: EvidenceIntegrity | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PromotionRequest:
        gates_raw = raw.get("gates")
        gates = tuple(
            GateOutcome.from_dict(g)
            for g in (gates_raw if isinstance(gates_raw, (list, tuple)) else ())
        )
        ev_raw = raw.get("evidence")
        evidence = (
            EvidenceIntegrity(
                body=ev_raw.get("body"),
                claimed_digest=str(ev_raw.get("claimed_digest", "")),
            )
            if isinstance(ev_raw, Mapping)
            else None
        )
        return cls(
            tier=str(raw.get("tier", "")),
            categories=_as_str_tuple(raw.get("categories")),
            gates=gates,
            implementer=str(raw.get("implementer", "")),
            verifier=str(raw.get("verifier", "")),
            approvers=_as_str_tuple(raw.get("approvers")),
            evidence=evidence,
        )


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #

# Reason codes (stable, machine-branchable prefixes). Interpolated values are runtime DATA.
REASON_EVIDENCE_MISSING = "evidence-missing"
REASON_EVIDENCE_DIGEST_MISMATCH = "evidence-digest-mismatch"
REASON_GATE_MISSING = "gate-missing"  # ":<id>"
REASON_GATE_FAILED = "gate-failed"  # ":<id>"
REASON_VERIFIER_EQUALS_IMPLEMENTER = "verifier-equals-implementer"
REASON_APPROVER_IS_AGENT = "approver-is-agent"  # ":<identity>"
REASON_APPROVER_NOT_ENROLLED = "approver-not-enrolled"  # ":<identity>"
REASON_IMPLEMENTER_EQUALS_APPROVER = "implementer-equals-approver"  # ":<identity>"
REASON_VERIFIER_EQUALS_APPROVER = "verifier-equals-approver"  # ":<identity>"
REASON_INSUFFICIENT_APPROVERS = "insufficient-approvers"  # ":<count>/<required>"


@dataclass(frozen=True)
class PromotionDecision:
    """The fail-closed verdict. ``allowed`` is true iff ``reasons`` is empty."""

    allowed: bool
    reasons: tuple[str, ...]
    consequential: bool
    required_approvers: int
    approver_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "consequential": self.consequential,
            "required_approvers": self.required_approvers,
            "approver_count": self.approver_count,
        }


def decide_promotion(
    request: PromotionRequest,
    policy: SegregationPolicy,
    profile: ConsequenceProfile,
) -> PromotionDecision:
    """Pure, default-deny promotion decision. NEVER performs a promotion/merge.

    Composes four independent conditions; the decision allows only when the reason set is empty:

      (a) evidence integrity — the content-addressed artifact is present and its claimed digest
          verifies (constant-time);
      (b) gate quorum — every profile-required gate is present AND passed;
      (c) segregation of duties — verifier ≠ implementer, and each approver resolves to an
          enrolled human (DENY-wins: never an agent), distinct from implementer and verifier;
      (d) approver floor — the count of DISTINCT enrolled-human approvers meets the consequence-
          driven requirement (≥2 for consequential, ≥1 otherwise; a target may raise, not lower).
    """
    consequential = profile.is_consequential(request.tier, request.categories)
    required = profile.required_approvers(request.tier, request.categories)
    reasons: list[str] = []

    # (a) evidence integrity — content-addressed tamper precondition.
    evidence = request.evidence
    if evidence is None or not evidence.present:
        reasons.append(REASON_EVIDENCE_MISSING)
    elif not evidence.verify():
        reasons.append(REASON_EVIDENCE_DIGEST_MISMATCH)

    # (b) gate quorum — every required gate present AND passed. Sorted for a deterministic order.
    outcomes = {_norm(g.id): g for g in request.gates}
    for gate_id in sorted(profile.required_gate_ids):
        gate = outcomes.get(gate_id)
        if gate is None:
            reasons.append(f"{REASON_GATE_MISSING}:{gate_id}")
        elif not gate.passed:
            reasons.append(f"{REASON_GATE_FAILED}:{gate_id}")

    # (c) segregation of duties. verifier↔implementer distinctness is independent of approvers
    # (covers the zero-approver case); reuse the policy's canonicalization so two aliases of one
    # principal are not mistaken for distinct.
    impl_c = policy.canonical(request.implementer)
    verf_c = policy.canonical(request.verifier)
    if request.implementer and request.verifier and impl_c == verf_c:
        reasons.append(REASON_VERIFIER_EQUALS_IMPLEMENTER)

    distinct_human_ids: set[str] = set()
    for approver in request.approvers:
        human = policy.resolve_human(approver)
        if human is None:
            # DENY-wins: an excluded (agent) identity is reported as an agent; anything else that
            # fails to resolve is simply not enrolled. Either way it can never approve.
            code = (
                REASON_APPROVER_IS_AGENT
                if (policy.is_excluded(approver) or policy.is_excluded(policy.canonical(approver)))
                else REASON_APPROVER_NOT_ENROLLED
            )
            reasons.append(f"{code}:{_norm(approver)}")
            continue
        if request.implementer and human == impl_c:
            reasons.append(f"{REASON_IMPLEMENTER_EQUALS_APPROVER}:{_norm(approver)}")
            continue
        if request.verifier and human == verf_c:
            reasons.append(f"{REASON_VERIFIER_EQUALS_APPROVER}:{_norm(approver)}")
            continue
        distinct_human_ids.add(human)

    approver_count = len(distinct_human_ids)

    # (d) the consequence-driven distinct-human floor.
    if approver_count < required:
        reasons.append(f"{REASON_INSUFFICIENT_APPROVERS}:{approver_count}/{required}")

    deduped = tuple(dict.fromkeys(reasons))  # de-dup, preserve first-seen order
    return PromotionDecision(
        allowed=not deduped,
        reasons=deduped,
        consequential=consequential,
        required_approvers=required,
        approver_count=approver_count,
    )
