"""Signed, scoped tool-policy enforcement for one factory run.

The three phase artifacts remain the only sources of intent. A tool policy is a
content-addressed enforcement projection: each rule cites the exact Architecture
Specification or Testing and Monitoring Strategy item it implements, and therefore cannot
originate or widen a requirement.

The core fixes the three tiers and the fail-closed mechanics while keeping every concrete tool,
scope, credential, route, and integration id as target data:

* every declared inventory item has exactly one tier;
* unknown tools and Verboten tools are denied;
* Allowed and Sign-off-required uses are bounded by exact opaque scope ids;
* Sign-off-required authority is human, scoped, content-addressed, and expiring;
* renewal is a new authorization record rather than an automatic extension; and
* every Verboten rule has a content-addressed denial probe demonstrating enforcement.

Scope ids are intentionally opaque. A target adapter must translate the proposed concrete
operation into a declared scope id *before* calling :func:`authorize_tool_invocation`; this
module then performs the deterministic pre-execution decision.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import SegregationPolicy, digest_obj, verify_digest
from factory_core.provenance import (
    CLAIM_CONSTRAINT,
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
    IntentBackreference,
    ProvenanceBundle,
    ProvenanceClaim,
    provenance_issue_is_gap,
    verify_intent_provenance,
)

TOOL_TIER_ALLOWED = "allowed"
TOOL_TIER_SIGNOFF_REQUIRED = "sign-off-required"
TOOL_TIER_VERBOTEN = "verboten"
TOOL_TIERS: tuple[str, ...] = (
    TOOL_TIER_ALLOWED,
    TOOL_TIER_SIGNOFF_REQUIRED,
    TOOL_TIER_VERBOTEN,
)

AUTHORIZATION_RUN = "run"
AUTHORIZATION_USE = "use"
AUTHORIZATION_MODES: tuple[str, ...] = (AUTHORIZATION_RUN, AUTHORIZATION_USE)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

TOOL_POLICY_GAP_PREFIXES: frozenset[str] = frozenset(
    {
        "tool-policy-rule-backreference-missing",
        "tool-policy-provenance-gap",
        "tool-unclassified",
        "verboten-denial-probe-missing",
    }
)


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _is_digest(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value))


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value)
    return (str(value),)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def tool_policy_issue_is_gap(issue: str) -> bool:
    """Return whether an issue is absence rather than invalid/negative authority."""

    return issue.split(":", 1)[0] in TOOL_POLICY_GAP_PREFIXES


@dataclass(frozen=True)
class ToolRule:
    """One inventory item's exact tier, scope ceiling, and intent authority."""

    tool_id: str
    tier: str
    scope_ids: frozenset[str] = frozenset()
    backreference: IntentBackreference | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_id", _normalize(self.tool_id))
        object.__setattr__(self, "tier", _normalize(self.tier))
        object.__setattr__(
            self,
            "scope_ids",
            frozenset(_normalize(scope) for scope in self.scope_ids if _normalize(scope)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "tier": self.tier,
            "scope_ids": sorted(self.scope_ids),
            "backreference": self.backreference.to_dict() if self.backreference else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ToolRule:
        raw_ref = raw.get("backreference")
        return cls(
            tool_id=str(raw.get("tool_id", "")),
            tier=str(raw.get("tier", "")),
            scope_ids=frozenset(_as_str_tuple(raw.get("scope_ids"))),
            backreference=(
                IntentBackreference.from_dict(raw_ref) if isinstance(raw_ref, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class ToolPolicy:
    """The signed, immutable-for-the-run capability projection."""

    policy_id: str
    version: str
    run_id: str
    issued_at: int
    expires_at: int
    signed_by: str
    independently_approved_by: str
    inventory_tool_ids: frozenset[str] = frozenset()
    rules: tuple[ToolRule, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _normalize(self.policy_id))
        object.__setattr__(self, "run_id", _normalize(self.run_id))
        object.__setattr__(
            self,
            "inventory_tool_ids",
            frozenset(
                _normalize(tool_id)
                for tool_id in self.inventory_tool_ids
                if _normalize(tool_id)
            ),
        )

    def body(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "run_id": self.run_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "signed_by": self.signed_by,
            "independently_approved_by": self.independently_approved_by,
            "inventory_tool_ids": sorted(self.inventory_tool_ids),
            "rules": [
                rule.to_dict()
                for rule in sorted(self.rules, key=lambda candidate: candidate.tool_id)
            ],
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ToolPolicy:
        return cls(
            policy_id=str(raw.get("policy_id", "")),
            version=str(raw.get("version", "")),
            run_id=str(raw.get("run_id", "")),
            issued_at=_as_int(raw.get("issued_at")),
            expires_at=_as_int(raw.get("expires_at")),
            signed_by=str(raw.get("signed_by", "")),
            independently_approved_by=str(raw.get("independently_approved_by", "")),
            inventory_tool_ids=frozenset(_as_str_tuple(raw.get("inventory_tool_ids"))),
            rules=tuple(ToolRule.from_dict(item) for item in _mapping_sequence(raw.get("rules"))),
        )


@dataclass(frozen=True)
class ToolAuthorization:
    """One fresh human authorization for a Sign-off-required tool."""

    authorization_id: str
    tool_id: str
    principal: str
    scope_ids: frozenset[str]
    mode: str
    issued_at: int
    expires_at: int
    authorized_by: str
    policy_digest: str
    run_id: str
    invocation_id: str = ""
    evidence: EvidenceIntegrity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization_id", _normalize(self.authorization_id))
        object.__setattr__(self, "tool_id", _normalize(self.tool_id))
        object.__setattr__(self, "mode", _normalize(self.mode))
        object.__setattr__(self, "run_id", _normalize(self.run_id))
        object.__setattr__(self, "invocation_id", _normalize(self.invocation_id))
        object.__setattr__(
            self,
            "scope_ids",
            frozenset(_normalize(scope) for scope in self.scope_ids if _normalize(scope)),
        )

    def authority_body(self) -> dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "tool_id": self.tool_id,
            "principal": self.principal,
            "scope_ids": sorted(self.scope_ids),
            "mode": self.mode,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "authorized_by": self.authorized_by,
            "policy_digest": self.policy_digest,
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.authority_body(),
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ToolAuthorization:
        evidence_raw = raw.get("evidence")
        return cls(
            authorization_id=str(raw.get("authorization_id", "")),
            tool_id=str(raw.get("tool_id", "")),
            principal=str(raw.get("principal", "")),
            scope_ids=frozenset(_as_str_tuple(raw.get("scope_ids"))),
            mode=str(raw.get("mode", "")),
            issued_at=_as_int(raw.get("issued_at")),
            expires_at=_as_int(raw.get("expires_at")),
            authorized_by=str(raw.get("authorized_by", "")),
            policy_digest=str(raw.get("policy_digest", "")),
            run_id=str(raw.get("run_id", "")),
            invocation_id=str(raw.get("invocation_id", "")),
            evidence=EvidenceIntegrity.from_dict(
                evidence_raw if isinstance(evidence_raw, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class DenialProbe:
    """Recorded attempt proving a Verboten capability is absent at the resource boundary."""

    probe_id: str
    tool_id: str
    scope_id: str
    attempted_at: int
    refused: bool
    policy_digest: str
    run_id: str
    evidence: EvidenceIntegrity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "probe_id", _normalize(self.probe_id))
        object.__setattr__(self, "tool_id", _normalize(self.tool_id))
        object.__setattr__(self, "scope_id", _normalize(self.scope_id))
        object.__setattr__(self, "run_id", _normalize(self.run_id))

    def authority_body(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "tool_id": self.tool_id,
            "scope_id": self.scope_id,
            "attempted_at": self.attempted_at,
            "refused": self.refused,
            "policy_digest": self.policy_digest,
            "run_id": self.run_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.authority_body(),
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DenialProbe:
        evidence_raw = raw.get("evidence")
        return cls(
            probe_id=str(raw.get("probe_id", "")),
            tool_id=str(raw.get("tool_id", "")),
            scope_id=str(raw.get("scope_id", "")),
            attempted_at=_as_int(raw.get("attempted_at")),
            refused=bool(raw.get("refused", False)),
            policy_digest=str(raw.get("policy_digest", "")),
            run_id=str(raw.get("run_id", "")),
            evidence=EvidenceIntegrity.from_dict(
                evidence_raw if isinstance(evidence_raw, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class ToolPolicyBundle:
    """Policy, external trust anchor, authorizations, probes, and phase authority.

    ``single_operator_anchor`` is the ratified single-operator disposition
    (2026-08-27): when the enrolled roster contains exactly one human, the
    independent-approval seat is filled by an externally signed, content-addressed
    envelope binding this exact policy digest — the runtime seam verifies the real
    signature; the pure core verifies the envelope binds THIS policy. It preserves
    what independent approval is for (the policy cannot be silently self-modified
    mid-run) while refusing to pretend independent human judgment exists — every
    decision over it carries a permanent disclosure report. With more than one
    enrolled human the anchor is invalid authority: it can never substitute for a
    real second human who exists.
    """

    policy: ToolPolicy
    trusted_policy_digest: str
    provenance: ProvenanceBundle
    authorizations: tuple[ToolAuthorization, ...] = ()
    denial_probes: tuple[DenialProbe, ...] = ()
    single_operator_anchor: EvidenceIntegrity | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.body(),
            "trusted_policy_digest": self.trusted_policy_digest,
            "provenance": self.provenance.to_dict(),
            "authorizations": [
                authorization.to_dict() for authorization in self.authorizations
            ],
            "denial_probes": [probe.to_dict() for probe in self.denial_probes],
            "single_operator_anchor": (
                self.single_operator_anchor.to_dict()
                if self.single_operator_anchor is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ToolPolicyBundle | None:
        raw_policy = raw.get("policy")
        raw_provenance = raw.get("provenance")
        if not isinstance(raw_policy, Mapping) or not isinstance(raw_provenance, Mapping):
            return None
        anchor_raw = raw.get("single_operator_anchor")
        return cls(
            policy=ToolPolicy.from_dict(raw_policy),
            trusted_policy_digest=str(raw.get("trusted_policy_digest", "")),
            provenance=ProvenanceBundle.from_dict(raw_provenance),
            authorizations=tuple(
                ToolAuthorization.from_dict(item)
                for item in _mapping_sequence(raw.get("authorizations"))
            ),
            denial_probes=tuple(
                DenialProbe.from_dict(item)
                for item in _mapping_sequence(raw.get("denial_probes"))
            ),
            single_operator_anchor=EvidenceIntegrity.from_dict(
                anchor_raw if isinstance(anchor_raw, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class ToolPolicyReport:
    """Fail-closed verification of one exact run policy.

    ``reports`` carries non-blocking disclosures (the single-operator anchor line
    among them) that every downstream decision must surface rather than absorb.
    """

    satisfied: bool
    policy_digest: str
    issues: tuple[str, ...]
    verified_tool_ids: tuple[str, ...]
    verified_authorization_ids: tuple[str, ...]
    verified_denial_probe_ids: tuple[str, ...]
    reports: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "policy_digest": self.policy_digest,
            "issues": list(self.issues),
            "verified_tool_ids": list(self.verified_tool_ids),
            "verified_authorization_ids": list(self.verified_authorization_ids),
            "verified_denial_probe_ids": list(self.verified_denial_probe_ids),
            "reports": list(self.reports),
        }


@dataclass(frozen=True)
class ToolInvocationDecision:
    """Pre-execution decision for one proposed invocation."""

    allowed: bool
    reason: str
    policy_digest: str
    authorization_id: str = ""


def verify_tool_policy(
    bundle: ToolPolicyBundle,
    identity_policy: SegregationPolicy,
    evaluated_at: int,
) -> ToolPolicyReport:
    """Verify policy authority, exact tier coverage, grants, and denial probes."""

    policy = bundle.policy
    issues: list[str] = []
    reports: list[str] = []
    verified_tools: list[str] = []
    verified_authorizations: list[str] = []
    verified_probes: list[str] = []

    if not policy.policy_id:
        issues.append("tool-policy-id-missing")
    if not policy.version.strip():
        issues.append("tool-policy-version-missing")
    if not policy.run_id:
        issues.append("tool-policy-run-id-missing")
    if policy.issued_at <= 0 or policy.expires_at <= policy.issued_at:
        issues.append("tool-policy-window-invalid")
    if evaluated_at <= 0 or not (policy.issued_at <= evaluated_at < policy.expires_at):
        issues.append("tool-policy-not-active")

    signer = identity_policy.resolve_human(policy.signed_by)
    if signer is None:
        issues.append("tool-policy-signer-not-enrolled")
    # Single-operator disposition (ratified 2026-08-27): with exactly one enrolled
    # human, an empty independent-approver seat may be filled by an externally
    # signed anchor binding this exact policy digest. Every other combination keeps
    # the two-distinct-humans rule: an anchor beside a multi-human roster can never
    # dodge a real second human, an anchor beside a named approver is ambiguous
    # authority, and the sole human naming themself independent approver is still a
    # lie. All of these are invalid authority, not gaps — fail-closed.
    single_operator = len(identity_policy.human_ids) == 1
    anchor = bundle.single_operator_anchor
    if anchor is not None and not single_operator:
        issues.append("tool-policy-anchor-with-multiple-humans")
    if anchor is not None and policy.independently_approved_by.strip():
        issues.append("tool-policy-anchor-and-approver-both-present")
    if single_operator and not policy.independently_approved_by.strip():
        if anchor is None or not anchor.present:
            issues.append("tool-policy-single-operator-anchor-missing")
        elif not anchor.verifies_binding({"policy_digest": policy.content_digest}):
            issues.append("tool-policy-single-operator-anchor-invalid")
        elif signer is not None:
            reports.append(
                "tool-policy-single-operator-anchored:"
                f"{signer}: independent human approval unavailable; "
                "policy digest externally anchored"
            )
    else:
        approver = identity_policy.resolve_human(policy.independently_approved_by)
        if approver is None:
            issues.append("tool-policy-independent-approver-not-enrolled")
        if signer is not None and approver is not None and signer == approver:
            issues.append("tool-policy-signer-equals-independent-approver")

    if not bundle.trusted_policy_digest:
        issues.append("tool-policy-trusted-digest-missing")
    elif (
        not _is_digest(bundle.trusted_policy_digest)
        or not verify_digest(policy.body(), bundle.trusted_policy_digest)
    ):
        issues.append("tool-policy-digest-mismatch")

    rule_index: dict[str, ToolRule] = {}
    rule_claims: list[ProvenanceClaim] = []
    artifact_phase = {
        artifact.artifact_id: artifact.phase for artifact in bundle.provenance.artifacts
    }
    for rule in policy.rules:
        if not rule.tool_id:
            issues.append("tool-policy-rule-id-missing")
            continue
        if rule.tool_id in rule_index:
            issues.append(f"tool-policy-rule-duplicate:{rule.tool_id}")
            continue
        rule_index[rule.tool_id] = rule
        if rule.tool_id not in policy.inventory_tool_ids:
            issues.append(f"tool-policy-rule-outside-inventory:{rule.tool_id}")
        if rule.tier not in TOOL_TIERS:
            issues.append(f"tool-policy-tier-invalid:{rule.tool_id}:{rule.tier}")
        if rule.tier == TOOL_TIER_VERBOTEN and rule.scope_ids:
            issues.append(f"verboten-rule-carries-grant-scope:{rule.tool_id}")
        if rule.tier in {TOOL_TIER_ALLOWED, TOOL_TIER_SIGNOFF_REQUIRED} and not rule.scope_ids:
            issues.append(f"tool-policy-scope-missing:{rule.tool_id}")
        if rule.backreference is None:
            issues.append(f"tool-policy-rule-backreference-missing:{rule.tool_id}")
        else:
            rule_claims.append(
                ProvenanceClaim(
                    claim_id=f"tool-policy:{rule.tool_id}",
                    kind=CLAIM_CONSTRAINT,
                    backreference=rule.backreference,
                )
            )
            if artifact_phase.get(rule.backreference.artifact_id) not in {
                PHASE_ARCHITECTURE,
                PHASE_OPERATIONAL_MATURITY,
            }:
                issues.append(f"tool-policy-authority-phase-invalid:{rule.tool_id}")

    for tool_id in sorted(policy.inventory_tool_ids - set(rule_index)):
        issues.append(f"tool-unclassified:{tool_id}")

    if rule_claims:
        provenance_report = verify_intent_provenance(
            bundle.provenance.artifacts,
            tuple(rule_claims),
            bundle.provenance.trusted_artifact_digests,
        )
        issues.extend(
            (
                f"tool-policy-provenance-gap:{issue}"
                if provenance_issue_is_gap(issue)
                else f"tool-policy-provenance-integrity:{issue}"
            )
            for issue in provenance_report.issues
        )
        if provenance_report.satisfied:
            verified_tools.extend(
                claim.claim_id.removeprefix("tool-policy:")
                for claim in provenance_report.resolved_claims
            )
    elif policy.inventory_tool_ids:
        issues.append("tool-policy-provenance:claims-empty")

    authorization_ids: set[str] = set()
    for authorization in bundle.authorizations:
        auth_id = authorization.authorization_id
        if not auth_id:
            issues.append("tool-authorization-id-missing")
            continue
        if auth_id in authorization_ids:
            issues.append(f"tool-authorization-duplicate:{auth_id}")
            continue
        authorization_ids.add(auth_id)
        authorization_rule = rule_index.get(authorization.tool_id)
        if authorization_rule is None:
            issues.append(f"tool-authorization-tool-unresolved:{auth_id}")
            continue
        authorization_valid = True
        if authorization_rule.tier != TOOL_TIER_SIGNOFF_REQUIRED:
            issues.append(
                f"tool-authorization-tier-invalid:{auth_id}:{authorization_rule.tier}"
            )
            authorization_valid = False
        if not authorization.principal.strip():
            issues.append(f"tool-authorization-principal-missing:{auth_id}")
            authorization_valid = False
        if authorization.mode not in AUTHORIZATION_MODES:
            issues.append(f"tool-authorization-mode-invalid:{auth_id}")
            authorization_valid = False
        if authorization.mode == AUTHORIZATION_USE and not authorization.invocation_id:
            issues.append(f"tool-authorization-invocation-id-missing:{auth_id}")
            authorization_valid = False
        if authorization.mode == AUTHORIZATION_RUN and authorization.invocation_id:
            issues.append(f"tool-authorization-run-carries-invocation:{auth_id}")
            authorization_valid = False
        if not authorization.scope_ids:
            issues.append(f"tool-authorization-scope-missing:{auth_id}")
            authorization_valid = False
        elif not authorization.scope_ids <= authorization_rule.scope_ids:
            issues.append(f"tool-authorization-scope-exceeds-policy:{auth_id}")
            authorization_valid = False
        if (
            authorization.issued_at < policy.issued_at
            or authorization.expires_at <= authorization.issued_at
            or authorization.expires_at > policy.expires_at
        ):
            issues.append(f"tool-authorization-window-invalid:{auth_id}")
            authorization_valid = False
        if authorization.policy_digest != policy.content_digest:
            issues.append(f"tool-authorization-policy-mismatch:{auth_id}")
            authorization_valid = False
        if authorization.run_id != policy.run_id:
            issues.append(f"tool-authorization-run-mismatch:{auth_id}")
            authorization_valid = False
        if identity_policy.resolve_human(authorization.authorized_by) is None:
            issues.append(f"tool-authorization-authorizer-not-enrolled:{auth_id}")
            authorization_valid = False
        if authorization.evidence is None or not authorization.evidence.present:
            issues.append(f"tool-authorization-evidence-missing:{auth_id}")
            authorization_valid = False
        elif not authorization.evidence.verify():
            issues.append(f"tool-authorization-evidence-digest-mismatch:{auth_id}")
            authorization_valid = False
        elif not authorization.evidence.verifies_binding(authorization.authority_body()):
            issues.append(f"tool-authorization-evidence-subject-mismatch:{auth_id}")
            authorization_valid = False

        if authorization_valid:
            verified_authorizations.append(auth_id)

    probes_by_tool: dict[str, list[DenialProbe]] = {}
    probe_ids: set[str] = set()
    for probe in bundle.denial_probes:
        if not probe.probe_id:
            issues.append("denial-probe-id-missing")
            continue
        if probe.probe_id in probe_ids:
            issues.append(f"denial-probe-duplicate:{probe.probe_id}")
            continue
        probe_ids.add(probe.probe_id)
        probe_rule = rule_index.get(probe.tool_id)
        if probe_rule is None or probe_rule.tier != TOOL_TIER_VERBOTEN:
            issues.append(f"denial-probe-tool-not-verboten:{probe.probe_id}")
            continue
        probes_by_tool.setdefault(probe.tool_id, []).append(probe)
        probe_valid = True
        if not probe.scope_id:
            issues.append(f"denial-probe-scope-missing:{probe.probe_id}")
            probe_valid = False
        if not (policy.issued_at <= probe.attempted_at < policy.expires_at):
            issues.append(f"denial-probe-time-outside-policy:{probe.probe_id}")
            probe_valid = False
        if not probe.refused:
            issues.append(f"verboten-denial-probe-executed:{probe.probe_id}")
            probe_valid = False
        if probe.policy_digest != policy.content_digest:
            issues.append(f"denial-probe-policy-mismatch:{probe.probe_id}")
            probe_valid = False
        if probe.run_id != policy.run_id:
            issues.append(f"denial-probe-run-mismatch:{probe.probe_id}")
            probe_valid = False
        if probe.evidence is None or not probe.evidence.present:
            issues.append(f"denial-probe-evidence-missing:{probe.probe_id}")
            probe_valid = False
        elif not probe.evidence.verify():
            issues.append(f"denial-probe-evidence-digest-mismatch:{probe.probe_id}")
            probe_valid = False
        elif not probe.evidence.verifies_binding(probe.authority_body()):
            issues.append(f"denial-probe-evidence-subject-mismatch:{probe.probe_id}")
            probe_valid = False
        if probe_valid:
            verified_probes.append(probe.probe_id)

    for tool_id, rule in sorted(rule_index.items()):
        if rule.tier == TOOL_TIER_VERBOTEN and not any(
            probe.probe_id in verified_probes for probe in probes_by_tool.get(tool_id, ())
        ):
            issues.append(f"verboten-denial-probe-missing:{tool_id}")

    unique_issues = tuple(dict.fromkeys(issues))
    return ToolPolicyReport(
        satisfied=not unique_issues,
        policy_digest=policy.content_digest,
        issues=unique_issues,
        verified_tool_ids=tuple(sorted(set(verified_tools))),
        verified_authorization_ids=tuple(sorted(set(verified_authorizations))),
        verified_denial_probe_ids=tuple(sorted(set(verified_probes))),
        reports=tuple(dict.fromkeys(reports)),
    )


def authorize_tool_invocation(
    bundle: ToolPolicyBundle,
    identity_policy: SegregationPolicy,
    *,
    principal: str,
    tool_id: str,
    scope_id: str,
    invocation_id: str,
    attempted_at: int,
) -> ToolInvocationDecision:
    """Decide one proposed call before it reaches the tool or external resource."""

    report = verify_tool_policy(bundle, identity_policy, attempted_at)
    policy = bundle.policy
    if not report.satisfied:
        return ToolInvocationDecision(
            allowed=False,
            reason=f"tool-policy-invalid:{report.issues[0]}",
            policy_digest=policy.content_digest,
        )

    normalized_tool = _normalize(tool_id)
    normalized_scope = _normalize(scope_id)
    normalized_invocation = _normalize(invocation_id)
    rule = next((item for item in policy.rules if item.tool_id == normalized_tool), None)
    if rule is None:
        return ToolInvocationDecision(
            allowed=False,
            reason="tool-unknown-default-verboten",
            policy_digest=policy.content_digest,
        )
    if rule.tier == TOOL_TIER_VERBOTEN:
        return ToolInvocationDecision(
            allowed=False,
            reason="tool-verboten",
            policy_digest=policy.content_digest,
        )
    if normalized_scope not in rule.scope_ids:
        return ToolInvocationDecision(
            allowed=False,
            reason="tool-scope-outside-policy",
            policy_digest=policy.content_digest,
        )
    if rule.tier == TOOL_TIER_ALLOWED:
        return ToolInvocationDecision(
            allowed=True,
            reason="tool-allowed-by-run-policy",
            policy_digest=policy.content_digest,
        )

    for authorization in bundle.authorizations:
        if authorization.authorization_id not in report.verified_authorization_ids:
            continue
        if authorization.tool_id != normalized_tool or authorization.principal != principal:
            continue
        if normalized_scope not in authorization.scope_ids:
            continue
        if not (authorization.issued_at <= attempted_at < authorization.expires_at):
            continue
        if (
            authorization.mode == AUTHORIZATION_USE
            and authorization.invocation_id != normalized_invocation
        ):
            continue
        return ToolInvocationDecision(
            allowed=True,
            reason="tool-signoff-authorized",
            policy_digest=policy.content_digest,
            authorization_id=authorization.authorization_id,
        )

    return ToolInvocationDecision(
        allowed=False,
        reason="tool-signoff-required",
        policy_digest=policy.content_digest,
    )
