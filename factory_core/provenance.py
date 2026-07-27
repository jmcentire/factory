"""Fail-closed provenance of intent over the three signed phase artifacts.

The factory doctrine requires every downstream requirement, constraint, task, and test
assertion to carry a resolvable backreference to the exact phase-artifact item that authorizes
it. This module implements the deterministic part of that rule:

* phase artifacts contain canonical intent items, not merely prose that "means roughly this";
* every canonical item has a content digest;
* downstream claims carry only a reference to that canonical item and its digest;
* the artifact bundle is accepted only against externally trusted content digests; and
* a missing phase, untrusted artifact, unresolved item, or mismatched digest is reported as an
  unsatisfied control.

This deliberately does **not** decide semantic equivalence. A digest cannot determine whether
two paraphrases mean the same thing, and pretending otherwise would launder an agent judgment
into a mechanical control. Semantic normalization happens while the human and Validator build
and ratify the phase artifact. Downstream systems retrieve the canonical statement through the
resolved reference; free-form downstream prose is not an authority.

The module is a pure substrate control, not an orchestration engine. It does not start agents,
enforce communication topology, verify signatures, or claim the three live lanes exist.
Signature and authority verification happen outside the core; the resulting trusted artifact
digests enter this function as data.

The verifier does not itself decide promotion. It classifies issues so the promotion policy can
distinguish an **absent link** (an evidence gap disposed of by surface criticality) from an
**invalid link** (an integrity failure that blocks every class). Both remain unsatisfied here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from factory_core.manifest import digest_obj, verify_digest

PHASE_PRODUCT_SPECIFICATION = "product-specification"
PHASE_ARCHITECTURE = "architecture"
PHASE_OPERATIONAL_MATURITY = "operational-maturity"

REQUIRED_PHASES: tuple[str, ...] = (
    PHASE_PRODUCT_SPECIFICATION,
    PHASE_ARCHITECTURE,
    PHASE_OPERATIONAL_MATURITY,
)

CLAIM_REQUIREMENT = "requirement"
CLAIM_CONSTRAINT = "constraint"
CLAIM_TASK = "task"
CLAIM_TEST_ASSERTION = "test-assertion"

SUPPORTED_CLAIM_KINDS: frozenset[str] = frozenset(
    {
        CLAIM_REQUIREMENT,
        CLAIM_CONSTRAINT,
        CLAIM_TASK,
        CLAIM_TEST_ASSERTION,
    }
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Absence is different from contradiction. These stable prefixes identify missing links whose
# promotion disposition belongs to the surface-criticality policy. Every other issue is an
# integrity defect: malformed, duplicated, unresolvable, mismatched, or fabricated.
PROVENANCE_GAP_PREFIXES: frozenset[str] = frozenset(
    {
        "artifact-items-empty",
        "artifact-untrusted",
        "artifact-version-missing",
        "backreference-artifact-id-missing",
        "backreference-item-id-missing",
        "backreference-missing",
        "claims-empty",
        "human-ratifier-missing",
        "intent-digest-missing",
        "phase-missing",
        "source-digest-missing",
        "validator-ratifier-missing",
    }
)


def _is_digest(value: str) -> bool:
    return bool(_SHA256_RE.fullmatch(value))


def provenance_issue_is_gap(issue: str) -> bool:
    """Return whether ``issue`` represents absence rather than invalid authority.

    The prefix vocabulary is part of the verifier's stable machine contract. Missing evidence
    is still an unsatisfied provenance check; this helper only tells the promotion layer which
    criticality disposition applies. Unknown prefixes fail closed as integrity defects.
    """

    return issue.split(":", 1)[0] in PROVENANCE_GAP_PREFIXES


@dataclass(frozen=True)
class IntentItem:
    """One canonical statement ratified inside a phase artifact.

    ``canonical_statement`` is the exact authority downstream work references. A reader may
    render or explain it differently, but the explanation never replaces this value.
    """

    item_id: str
    canonical_statement: str

    @property
    def intent_digest(self) -> str:
        """Content address of the canonical statement, independent of its display location."""
        return digest_obj({"canonical_statement": self.canonical_statement})

    def to_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "canonical_statement": self.canonical_statement,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> IntentItem:
        return cls(
            item_id=str(raw.get("item_id", "")),
            canonical_statement=str(raw.get("canonical_statement", "")),
        )


@dataclass(frozen=True)
class PhaseArtifact:
    """One ratified artifact from the product, architecture, or operational-maturity phase.

    ``source_digest`` binds the translation to the preserved verbatim source. The ratifier
    identities are included in the content address, but this core does not self-attest that
    either identity is authentic. The caller supplies the externally trusted content digest
    after its signature/authority system has verified the artifact.
    """

    artifact_id: str
    phase: str
    version: str
    source_digest: str
    human_ratifier: str
    validator_ratifier: str
    items: tuple[IntentItem, ...] = ()

    def body(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "phase": self.phase,
            "version": self.version,
            "source_digest": self.source_digest,
            "human_ratifier": self.human_ratifier,
            "validator_ratifier": self.validator_ratifier,
            "items": [item.to_dict() for item in self.items],
        }

    @property
    def content_digest(self) -> str:
        return digest_obj(self.body())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PhaseArtifact:
        raw_items = raw.get("items")
        items = tuple(
            IntentItem.from_dict(item)
            for item in (raw_items if isinstance(raw_items, Sequence) else ())
            if isinstance(item, Mapping)
        )
        return cls(
            artifact_id=str(raw.get("artifact_id", "")),
            phase=str(raw.get("phase", "")),
            version=str(raw.get("version", "")),
            source_digest=str(raw.get("source_digest", "")),
            human_ratifier=str(raw.get("human_ratifier", "")),
            validator_ratifier=str(raw.get("validator_ratifier", "")),
            items=items,
        )


@dataclass(frozen=True)
class IntentBackreference:
    """Resolvable pointer carried by downstream work.

    ``intent_digest`` prevents a stable item id from being replayed after its canonical
    statement changes.
    """

    artifact_id: str
    item_id: str
    intent_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "item_id": self.item_id,
            "intent_digest": self.intent_digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> IntentBackreference:
        return cls(
            artifact_id=str(raw.get("artifact_id", "")),
            item_id=str(raw.get("item_id", "")),
            intent_digest=str(raw.get("intent_digest", "")),
        )


@dataclass(frozen=True)
class ProvenanceClaim:
    """A downstream artifact's assertion that it is authorized by one canonical intent item.

    The claim intentionally has no authoritative free-form statement. Consumers obtain the
    exact statement from :class:`ResolvedClaim` after verification succeeds.
    """

    claim_id: str
    kind: str
    backreference: IntentBackreference | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "backreference": self.backreference.to_dict() if self.backreference else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProvenanceClaim:
        raw_ref = raw.get("backreference")
        return cls(
            claim_id=str(raw.get("claim_id", "")),
            kind=str(raw.get("kind", "")),
            backreference=(
                IntentBackreference.from_dict(raw_ref) if isinstance(raw_ref, Mapping) else None
            ),
        )


@dataclass(frozen=True)
class ResolvedClaim:
    """A claim whose authority was re-derived from a trusted phase artifact."""

    claim_id: str
    kind: str
    artifact_id: str
    item_id: str
    intent_digest: str
    canonical_statement: str

    def to_dict(self) -> dict[str, str]:
        return {
            "claim_id": self.claim_id,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "item_id": self.item_id,
            "intent_digest": self.intent_digest,
            "canonical_statement": self.canonical_statement,
        }


@dataclass(frozen=True)
class ProvenanceReport:
    """Fail-closed verification result. ``satisfied`` is true iff no issue exists."""

    satisfied: bool
    issues: tuple[str, ...]
    resolved_claims: tuple[ResolvedClaim, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "issues": list(self.issues),
            "resolved_claims": [claim.to_dict() for claim in self.resolved_claims],
        }


@dataclass(frozen=True)
class ProvenanceBundle:
    """The complete input needed to re-derive intent provenance at a gate.

    Keeping the artifacts, downstream claims, and external-authority digest map together makes
    it harder for a caller to accidentally verify one bundle and promote another.
    """

    artifacts: tuple[PhaseArtifact, ...] = ()
    claims: tuple[ProvenanceClaim, ...] = ()
    trusted_artifact_digests: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trusted_artifact_digests",
            MappingProxyType(dict(self.trusted_artifact_digests)),
        )

    def verify(self) -> ProvenanceReport:
        return verify_intent_provenance(
            self.artifacts,
            self.claims,
            self.trusted_artifact_digests,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [artifact.body() for artifact in self.artifacts],
            "claims": [claim.to_dict() for claim in self.claims],
            "trusted_artifact_digests": dict(self.trusted_artifact_digests),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProvenanceBundle:
        raw_artifacts = raw.get("artifacts")
        raw_claims = raw.get("claims")
        raw_trusted = raw.get("trusted_artifact_digests")
        artifacts = tuple(
            PhaseArtifact.from_dict(artifact)
            for artifact in (raw_artifacts if isinstance(raw_artifacts, Sequence) else ())
            if isinstance(artifact, Mapping)
        )
        claims = tuple(
            ProvenanceClaim.from_dict(claim)
            for claim in (raw_claims if isinstance(raw_claims, Sequence) else ())
            if isinstance(claim, Mapping)
        )
        trusted = (
            {str(key): str(value) for key, value in raw_trusted.items()}
            if isinstance(raw_trusted, Mapping)
            else {}
        )
        return cls(
            artifacts=artifacts,
            claims=claims,
            trusted_artifact_digests=trusted,
        )


def verify_intent_provenance(
    artifacts: Sequence[PhaseArtifact],
    claims: Sequence[ProvenanceClaim],
    trusted_artifact_digests: Mapping[str, str],
) -> ProvenanceReport:
    """Resolve every claim against exactly one trusted artifact for each required phase.

    ``trusted_artifact_digests`` is an external-authority input. Merely carrying a
    ``content_digest`` inside the artifact would be self-attestation, so an artifact absent
    from this mapping is untrusted and blocks the run.

    Issue strings have stable machine-branchable prefixes followed by the affected id.
    The function accumulates all structural defects for a useful decision package, but
    ``satisfied`` remains false if any one exists.
    """

    issues: list[str] = []
    artifact_index: dict[str, PhaseArtifact] = {}
    item_index: dict[tuple[str, str], IntentItem] = {}
    phase_counts = {phase: 0 for phase in REQUIRED_PHASES}

    for artifact in artifacts:
        artifact_id = artifact.artifact_id.strip()
        if not artifact_id:
            issues.append("artifact-id-missing")
            continue
        if artifact_id in artifact_index:
            issues.append(f"artifact-id-duplicate:{artifact_id}")
            continue
        artifact_index[artifact_id] = artifact

        if artifact.phase not in phase_counts:
            issues.append(f"phase-unsupported:{artifact_id}:{artifact.phase}")
        else:
            phase_counts[artifact.phase] += 1

        if not artifact.version.strip():
            issues.append(f"artifact-version-missing:{artifact_id}")
        if not artifact.source_digest:
            issues.append(f"source-digest-missing:{artifact_id}")
        elif not _is_digest(artifact.source_digest):
            issues.append(f"source-digest-invalid:{artifact_id}")
        if not artifact.human_ratifier.strip():
            issues.append(f"human-ratifier-missing:{artifact_id}")
        if not artifact.validator_ratifier.strip():
            issues.append(f"validator-ratifier-missing:{artifact_id}")
        if (
            artifact.human_ratifier.strip()
            and artifact.validator_ratifier.strip()
            and artifact.human_ratifier.strip() == artifact.validator_ratifier.strip()
        ):
            issues.append(f"ratifier-overlap:{artifact_id}")

        trusted_digest = trusted_artifact_digests.get(artifact_id, "")
        if not trusted_digest:
            issues.append(f"artifact-untrusted:{artifact_id}")
        elif not _is_digest(trusted_digest) or not verify_digest(artifact.body(), trusted_digest):
            issues.append(f"artifact-digest-mismatch:{artifact_id}")

        if not artifact.items:
            issues.append(f"artifact-items-empty:{artifact_id}")
        seen_item_ids: set[str] = set()
        for item in artifact.items:
            item_id = item.item_id.strip()
            if not item_id:
                issues.append(f"item-id-missing:{artifact_id}")
                continue
            if item_id in seen_item_ids:
                issues.append(f"item-id-duplicate:{artifact_id}:{item_id}")
                continue
            seen_item_ids.add(item_id)
            if not item.canonical_statement.strip():
                issues.append(f"canonical-statement-missing:{artifact_id}:{item_id}")
                continue
            item_index[(artifact_id, item_id)] = item

    for phase, count in phase_counts.items():
        if count == 0:
            issues.append(f"phase-missing:{phase}")
        elif count > 1:
            issues.append(f"phase-duplicate:{phase}:{count}")

    if not claims:
        issues.append("claims-empty")

    resolved: list[ResolvedClaim] = []
    seen_claim_ids: set[str] = set()
    for claim in claims:
        claim_id = claim.claim_id.strip()
        if not claim_id:
            issues.append("claim-id-missing")
            continue
        if claim_id in seen_claim_ids:
            issues.append(f"claim-id-duplicate:{claim_id}")
            continue
        seen_claim_ids.add(claim_id)

        if claim.kind not in SUPPORTED_CLAIM_KINDS:
            issues.append(f"claim-kind-unsupported:{claim_id}:{claim.kind}")

        ref = claim.backreference
        if ref is None:
            issues.append(f"backreference-missing:{claim_id}")
            continue
        if not ref.artifact_id.strip():
            issues.append(f"backreference-artifact-id-missing:{claim_id}")
            continue
        if not ref.item_id.strip():
            issues.append(f"backreference-item-id-missing:{claim_id}")
            continue
        if not ref.intent_digest:
            issues.append(f"intent-digest-missing:{claim_id}:{ref.artifact_id}:{ref.item_id}")
            continue
        if ref.artifact_id not in artifact_index:
            issues.append(f"artifact-unresolved:{claim_id}:{ref.artifact_id}")
            continue
        resolved_item = item_index.get((ref.artifact_id, ref.item_id))
        if resolved_item is None:
            issues.append(f"item-unresolved:{claim_id}:{ref.artifact_id}:{ref.item_id}")
            continue
        expected_digest = resolved_item.intent_digest
        if not _is_digest(ref.intent_digest) or ref.intent_digest != expected_digest:
            issues.append(f"intent-digest-mismatch:{claim_id}:{ref.artifact_id}:{ref.item_id}")
            continue
        resolved.append(
            ResolvedClaim(
                claim_id=claim_id,
                kind=claim.kind,
                artifact_id=ref.artifact_id,
                item_id=ref.item_id,
                intent_digest=expected_digest,
                canonical_statement=resolved_item.canonical_statement,
            )
        )

    unique_issues = tuple(dict.fromkeys(issues))
    return ProvenanceReport(
        satisfied=not unique_issues,
        issues=unique_issues,
        resolved_claims=tuple(resolved),
    )
