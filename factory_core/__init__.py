"""factory_core — the founder-owned, generic software-factory core.

A standalone, portable package that imports **nothing target-specific**. Every per-target
input (repo coordinates, working-agreement docs, compliance rules, role bindings, IdP config)
is data loaded at runtime through the adapter seams, never a code dependency. Deleting every
target pack leaves this package importable, testable, and green — that is the portability
guarantee, enforced by ``scripts/check_core_purity.py``.

Public surface:
  * manifest — the content-addressed, hash-chained, SoD-enforcing evidence ledger
  * target   — the TargetManifest loader (data in, never code)
  * adapters — the five ``typing.Protocol`` seams for all target contact
  * roles    — the capability/role model schema (grants are per-target data)
  * invariant_kernel — the neutral invariant IR, composition ledger, and a built-in
    graph-reachability analyzer (invariants carry their own start/forbidden roles + degree
    as data; the core names no target role, invariant, or annotation)
  * contract — neutral path normalization + forward/reverse FE<->BE contract diff, with a
    data-driven excuse classifier (inventories come from the adapter seams; rules are data)
  * completeness — a neutral inventory-row status lattice + falsifiable launch-readiness
    predicate (rows come from the adapter seams; the core owns only the aggregation)
  * comprehensiveness — a deterministic, injection-resistant intake-completeness gate: an
    ordered registry of structural field predicates (never an LLM); fields/thresholds are data
  * promotion — the fail-closed, default-deny merge/promotion decision: gate quorum + a
    consequence-driven distinct-human approver floor (reuses the manifest SegregationPolicy;
    tiers/categories/thresholds are data)
"""

from __future__ import annotations

from factory_core.adapters import (
    ADAPTER_PROTOCOLS,
    ArtifactSink,
    ComplianceAdapter,
    IdpAdapter,
    KnowledgeAdapter,
    RepoAdapter,
)
from factory_core.completeness import (
    STATUS_DECLARED,
    STATUS_EXCUSED,
    STATUS_GAP,
    STATUS_PARTIAL,
    STATUS_PROVED,
    DimensionSummary,
    Inventory,
    InventoryRow,
    LaunchReadiness,
    launch_ready,
)
from factory_core.comprehensiveness import (
    VERDICT_COMPREHENSIVE,
    VERDICT_NEEDS_INFO,
    ComprehensivenessError,
    ComprehensivenessGate,
    ComprehensivenessResult,
    ConditionalSpec,
    FieldGap,
    SubstanceRequirement,
    SubstanceSpec,
    is_substantive,
)
from factory_core.contract import (
    CallEdge,
    Classification,
    ContractReport,
    Endpoint,
    ExcuseClassifier,
    ExcuseRule,
    ForwardReport,
    ReverseReport,
    check_contract,
    forward_contract,
    normalize_path,
    reverse_contract,
)
from factory_core.invariant_kernel import (
    AnalysisResult,
    Analyzer,
    CapabilityDelta,
    CapabilityFlow,
    CapabilityLedger,
    ComposedModel,
    FidelityResult,
    GraphNode,
    InvariantKernel,
    ReachabilityAnalyzer,
    ReachabilityInvariant,
    SourceFacts,
    SourceFlowFact,
    Unsupported,
    Violation,
    check_delta_fidelity,
    load_delta,
)
from factory_core.invariant_kernel import analyze as analyze_invariants
from factory_core.manifest import (
    Ledger,
    LedgerEntry,
    SegregationError,
    SegregationPolicy,
    digest_bytes,
    digest_obj,
    verify_digest,
    verify_ledger,
)
from factory_core.promotion import (
    BASELINE_APPROVER_FLOOR,
    CONSEQUENTIAL_APPROVER_FLOOR,
    ConsequenceProfile,
    EvidenceIntegrity,
    GateOutcome,
    PromotionDecision,
    PromotionError,
    PromotionRequest,
    decide_promotion,
)
from factory_core.roles import (
    Capability,
    CapabilityCatalog,
    Grant,
    Role,
    RoleModel,
    RoleModelError,
)
from factory_core.target import (
    TargetManifest,
    TargetManifestError,
    load_target_manifest,
)

__version__ = "0.0.0"

__all__ = [
    "ADAPTER_PROTOCOLS",
    "AnalysisResult",
    "Analyzer",
    "ArtifactSink",
    "BASELINE_APPROVER_FLOOR",
    "CONSEQUENTIAL_APPROVER_FLOOR",
    "CallEdge",
    "Capability",
    "CapabilityCatalog",
    "CapabilityDelta",
    "CapabilityFlow",
    "CapabilityLedger",
    "Classification",
    "ComplianceAdapter",
    "ComposedModel",
    "ComprehensivenessError",
    "ComprehensivenessGate",
    "ComprehensivenessResult",
    "ConditionalSpec",
    "ConsequenceProfile",
    "ContractReport",
    "DimensionSummary",
    "Endpoint",
    "EvidenceIntegrity",
    "ExcuseClassifier",
    "ExcuseRule",
    "FidelityResult",
    "FieldGap",
    "ForwardReport",
    "GateOutcome",
    "Grant",
    "GraphNode",
    "IdpAdapter",
    "Inventory",
    "InventoryRow",
    "InvariantKernel",
    "KnowledgeAdapter",
    "LaunchReadiness",
    "Ledger",
    "LedgerEntry",
    "PromotionDecision",
    "PromotionError",
    "PromotionRequest",
    "ReachabilityAnalyzer",
    "ReachabilityInvariant",
    "RepoAdapter",
    "ReverseReport",
    "Role",
    "RoleModel",
    "RoleModelError",
    "STATUS_DECLARED",
    "STATUS_EXCUSED",
    "STATUS_GAP",
    "STATUS_PARTIAL",
    "STATUS_PROVED",
    "SegregationError",
    "SegregationPolicy",
    "SourceFacts",
    "SourceFlowFact",
    "SubstanceRequirement",
    "SubstanceSpec",
    "TargetManifest",
    "TargetManifestError",
    "Unsupported",
    "VERDICT_COMPREHENSIVE",
    "VERDICT_NEEDS_INFO",
    "Violation",
    "analyze_invariants",
    "check_contract",
    "check_delta_fidelity",
    "decide_promotion",
    "digest_bytes",
    "digest_obj",
    "forward_contract",
    "is_substantive",
    "launch_ready",
    "load_delta",
    "load_target_manifest",
    "normalize_path",
    "reverse_contract",
    "verify_digest",
    "verify_ledger",
    "__version__",
]
