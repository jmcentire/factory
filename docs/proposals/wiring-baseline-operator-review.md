# Wiring baseline — operator review

Task `3910eaa6c7e5`, item 3: the wiring audit's baseline has grown to 77 entries.
Deciding which represent genuine dead code (delete), intentional public API awaiting
a consumer (leave baselined, or wire it), or a real integration gap (wire it) requires
codebase history and intent this tool cannot infer — that decision is the operator's,
not something this pass resolves unilaterally. This report exists to make that
decision cheap, not to make it.

## Summary

| Class | Count |
|---|---|
| `zero-caller-export` | 73 |
| `unreachable-module` | 2 |
| `unresolved-reference` | 2 |
| **Total** | **77** |

73 of 77 carry the justification *"pre-existing at gate adoption (2026-08-27);
grandfathered pending operator review"* — i.e., they predate the wiring-audit tool
itself and have never been individually reviewed. The remaining 4 are today's own
new modules (`verdict.py`, `handover.py`, `qualification.py`), already tracked by
kindex task `e389b4905e30` (done — CLI wiring for `verdict`/`qualify`) and
`c7c8e43a6d32` (done); their residual unwired symbols below are the *type/dataclass*
exports those commands don't directly import (return types, not entrypoints
themselves) — expected, not a surprise finding.

## `zero-caller-export`, grouped by file (pre-existing bucket, 73 entries)

Two natural categories emerge from the pattern, but the tool cannot tell them apart
— that judgment is the ask:

- **A. Custom `Error`/`Exception` subclasses (13)** — `HandoverError`,
  `QualificationError`, `VerdictError`, `BrokerError`, `IsolationError`, `LaneError`,
  `OrchestrationError`, `OrchestratorProjectionError`, `ProjectionBundleError`,
  `RepairSupervisorError`, `ResumeVerificationError`, `RunnerFailureEvidenceError`,
  `StateQualificationError`. These are near-certainly fine to leave baselined
  indefinitely: an exception class's job is to be raised inside its own module and
  caught by name elsewhere, and the audit's reference tracking only credits a
  *cross-module* reference — a purely-internal `raise SomeError(...)` doesn't count
  as a caller by design (the same reachability standard applied to everything else).
  **Recommendation: accept as a standing, low-risk baseline category rather than
  reviewing individually** — but the operator's call, not assumed.

- **B. Console-script `main` functions (2)** — `factory_runtime/cli.py:main`,
  `factory_runtime/runner_failure.py:main`. Invoked via `if __name__ == "__main__"` or
  a packaging entry point, not a static in-tree reference. Same recommendation as A.

- **C. Everything else (58)**, by file — this is the real review surface:

| File | Count | Symbols |
|---|---|---|
| `factory_runtime/broker.py` | 6 | `BrokerCapabilityHandle`, `BrokerEffect`, `BrokerOperation`, `BrokerRegistry`, `load_broker_capability` (+`BrokerError`, counted in A) |
| `factory_core/verdict.py` | 4 | `AdequacyCriterion`, `CoverageTerritory`, `FiredProbe`, `verdict_rank` (`VerdictError`, `PromotionFloorLike` counted separately) |
| `factory_runtime/lanes.py` | 4 | `FrozenValidatorExecution`, `LaneRole`, `freeze_validator_execution`, `temporary_build_loop_root` (+`LaneError`, in A) |
| `factory_core/completeness.py` | 4 | `is_complete`, `meet`, `normalize_status`, `status_rank` |
| `factory_core/handover.py` | 3 | `DoneComposition`, `HandoverScope`, `reserved_token_violation` (+`HandoverError`, in A) |
| `factory_core/invariant_kernel.py` | 4 | `FidelityMismatch`, `LedgerFlow`, `TraceStep`, `validate_delta_dict` |
| `factory_runtime/repair.py` | 3 | `AttemptRunner`, `RepairPlanner`, `ValidatorLaunchRepairer` (+`RepairSupervisorError`, in A) |
| `factory_runtime/acceptance_obligations.py` | 3 | `StoredAcceptanceCatalog`, `ValidatorExecutionFile`, `verify_acceptance_obligation_report` |
| `factory_runtime/orchestrator.py` | 2 | `FactoryOrchestrator`, `digest_artifact_tree` (+`OrchestrationError`, in A) |
| `factory_runtime/runner.py` | 3 | `CodexRunnerAdapter`, `RunnerExecutableSnapshot`, `RunnerReceipt` |
| `factory_runtime/state_qualification.py` | 2 | `qualification_executor_digest`, `scenario_set_digest` (+`StateQualificationError`, in A) |
| `factory_core/roles.py` | 2 | `build_grants`, `build_roles` |
| `factory_runtime/evidence_plane.py` | 2 | `EvidenceVerificationReceipt`, `VerifiedEvidenceEnvelope` |
| `factory_runtime/generation.py` | 2 | `PreparedGeneration`, `build_input_document` |
| `factory_runtime/promotion_gate.py` | 2 | `decide`, `verify_chain_anchor` |
| `factory_runtime/workflow.py` | 2 | `StoredRatification`, `VerifiedRepairBrief` |
| `factory_core/contract.py` | 1 | `normalize_method` |
| `factory_core/independence.py` | 1 | `normalize_independence_label` |
| `factory_core/manifest.py` | 1 | `digest_file` |
| `factory_runtime/authority.py` | 1 | `Principal` |
| `factory_runtime/isolation.py` | 1 | (`IsolationError`, in A — file otherwise clean) |
| `factory_runtime/orchestrator_projection.py` | 1 | (`OrchestratorProjectionError`, in A) |
| `factory_runtime/projection_bundle.py` | 1 | (`ProjectionBundleError`, in A) |
| `factory_runtime/resume.py` | 1 | (`ResumeVerificationError`, in A) |
| `factory_runtime/state_admission.py` | 1 | `DependencyRule` |
| `factory_runtime/test_change_authority.py` | 1 | `StoredTestChangeAuthorization` |
| `factory_runtime/transition_obligations.py` | 1 | `require_transition_inputs` |

**`unreachable-module` (2):** `factory_runtime/schemas/__init__.py` (schema JSON is
loaded by filesystem path, not import — confirmed at gate adoption), plus one entry
double-counted with the runner_failure.py file above.

**`unresolved-reference` (2):** `factory_runtime/broker.py`, `factory_runtime/cli.py`
— both confirmed genuine dynamic-name `getattr` call sites at gate adoption, not
resolvable statically by construction; these are permanent, not a review item.

## What this report is asking for

Three dispositions per item (or per file, where a whole file's surface moves
together): **(1) delete** — genuinely dead, no longer needed; **(2) wire** — a real
caller should exist and doesn't yet, file as a task; **(3) leave baselined** — an
intentional public surface (library API, a class instantiated only by a caller
outside these two packages, a Protocol/interface implementation) that this tool's
reachable-from-two-package-entrypoints model was never going to credit. Category A
(exceptions) and B (`main` functions) are recommended for blanket disposition 3
without per-item review, pending the operator's agreement.
