"""Feasibility preflight — the early NO (remediation plan §1.1/§1.1b).

The founder's criterion: every reason a run would die must surface at intake,
as cheaply as possible — a NO at hour 0 is the point. This module is a PURE
function over ratified, signed inputs: no prompt, role instruction, or
attestation anywhere in the loop; the verdict is derived from the same tables
the late gates use, so an operator who ignores a NO meets the identical reason
code at the existing gate.

Three-way output discipline (check_wiring's tri-state precedent):
- ``hard_no``      — PASS or ``__DONE__`` is PROVABLY unreachable from
                     ratification facts alone (surface-set-independent).
- ``disclosures``  — surface-scoped: "NO if any Critical surface is disturbed"
                     phrased at T=0 (e.g. the one-human/Critical approver
                     collision), never a silent late death.
- ``not_applicable`` — an input group was absent, so its checks DID NOT RUN.
                     "Could not check" is loud and distinct from "passed".

The report is UNSIGNED and never auto-appended to any ledger — it enters the
record only as an explicitly human-signed artifact (provenance demotion, not a
claimed structural impossibility). The ceiling disposition states the best
verdict this run can possibly reach given what is ratified today.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from factory_core.build_plan import BuildPlan
from factory_core.criticality import CriticalityProfile
from factory_core.manifest import SegregationPolicy
from factory_core.verdict import (
    TERRITORY_COVERED,
    TERRITORY_KIND_SCENARIO,
    TERRITORY_UNCOVERED,
    VERDICT_BLOCK,
    VERDICT_PASS,
    VERDICT_PASS_ON_COVERED,
    CoverageMap,
    normalize_label,
)
from factory_runtime.generation import _signal_knob_issues


@dataclass(frozen=True)
class SignalDeadlineFacts:
    """Signal-deadline facts probed at the model-admission door (plan 4.1d).

    The 0.4 mechanical consequence: pass-index >= deadline with no NO-relevant
    signal and residual blockers present is a HOST REFUSAL of the next
    BUILDING/model admission — never only a pager. ``signal_known`` and
    ``blockers_known`` keep the tri-state honest: an unreadable registry or
    readiness snapshot is "could not check", loud, never a silent pass.
    """

    passes: int = 0
    deadline: int | None = None
    signal_known: bool = False
    signal_present: bool = False
    blockers_known: bool = False
    residual_blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class LivenessFacts:
    """Dead-run facts probed from the filesystem (impure prober below);
    the preflight core stays a pure function over these."""

    state: str = ""
    build_attempt_count: int = 0
    build_attempt_limit: int = 0
    guard_residue: tuple[str, ...] = ()
    ledger_error: str = ""
    chain_error: str = ""


@dataclass(frozen=True)
class PreflightFinding:
    code: str
    subject: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "detail": self.detail}


@dataclass(frozen=True)
class PreflightReport:
    go: bool
    ceiling: str
    hard_no: tuple[PreflightFinding, ...] = ()
    disclosures: tuple[PreflightFinding, ...] = ()
    notes: tuple[PreflightFinding, ...] = ()
    not_applicable: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "factory-preflight-report/1",
            "go": self.go,
            "ceiling": self.ceiling,
            "hard_no": [finding.to_dict() for finding in self.hard_no],
            "disclosures": [finding.to_dict() for finding in self.disclosures],
            "notes": [finding.to_dict() for finding in self.notes],
            "not_applicable": list(self.not_applicable),
        }


def run_preflight(
    *,
    coverage: CoverageMap | None = None,
    profile: CriticalityProfile | None = None,
    policy: SegregationPolicy | None = None,
    target_build: Mapping[str, Any] | None = None,
    plan_max_build_attempts: int | None = None,
    plan: BuildPlan | None = None,
    liveness: LivenessFacts | None = None,
    signal_deadline: SignalDeadlineFacts | None = None,
) -> PreflightReport:
    """Compute the intake verdict from ratification-time facts only."""

    hard_no: list[PreflightFinding] = []
    disclosures: list[PreflightFinding] = []
    notes: list[PreflightFinding] = []
    not_applicable: list[str] = []

    # --- verdict reachability (§1.1b, the two-islands bridge) --------------------
    ceiling = VERDICT_PASS
    if coverage is None:
        not_applicable.append("coverage-map")
        ceiling = "unknown"
        disclosures.append(
            PreflightFinding(
                "preflight-reachability-unverified",
                "coverage-map",
                "no ratified coverage map was supplied: PASS/__DONE__ "
                "reachability was NOT verified — a GO here clears nothing "
                "about the verdict layer",
            )
        )
    else:
        criteria_ids = {normalize_label(c.territory_id) for c in coverage.adequacy}
        uncovered = [
            territory
            for territory in coverage.territories
            if territory.status == TERRITORY_UNCOVERED
        ]
        for territory in uncovered:
            if normalize_label(territory.territory_id) not in criteria_ids:
                hard_no.append(
                    PreflightFinding(
                        "preflight-pass-unreachable",
                        normalize_label(territory.territory_id),
                        "uncovered territory has no ratified adequacy criterion: "
                        "no receipt can ever clear it, so PASS is provably "
                        "unreachable (the 127-hour case, caught at hour 0)",
                    )
                )
        if uncovered:
            ceiling = VERDICT_PASS_ON_COVERED
        if not any(
            territory.kind == TERRITORY_KIND_SCENARIO
            and territory.status == TERRITORY_COVERED
            for territory in coverage.territories
        ):
            # The post-run auditor's pure predicate (audit.py), lifted to a
            # pre-construction call site; the auditor itself stays non-gating.
            hard_no.append(
                PreflightFinding(
                    "preflight-purpose-untested",
                    "scenario-coverage",
                    "no covered scenario territory: the run's one-sentence "
                    "purpose has no planned test, so the forced first line can "
                    "never be YES",
                )
            )
        if not coverage.verb_ids:
            hard_no.append(
                PreflightFinding(
                    "preflight-done-unreachable",
                    "verb-ids",
                    "ratified verb set is empty: compose_done refuses an empty "
                    "verb union, so __DONE__ is provably unreachable",
                )
            )

    # --- attempt-ceiling and signal-knob consistency ------------------------------
    if target_build is None:
        not_applicable.append("attempt-ceilings")
    else:
        target_limit = target_build.get("max_attempts")
        if (
            isinstance(target_limit, int)
            and not isinstance(target_limit, bool)
            and plan_max_build_attempts is not None
            and plan_max_build_attempts > target_limit
        ):
            hard_no.append(
                PreflightFinding(
                    "preflight-attempt-ceiling-inconsistent",
                    "build-plan",
                    f"plan max_build_attempts {plan_max_build_attempts} exceeds "
                    f"the target ABI ceiling {target_limit}",
                )
            )
        for issue in _signal_knob_issues(target_build, None):
            hard_no.append(
                PreflightFinding(
                    "preflight-signal-knobs",
                    issue,
                    "signal-deadline knobs are invalid or undeclared at intake",
                )
            )

    # --- critical approver capacity (§1.1 class split, kindex 956b08784b09) -------
    if profile is None or policy is None:
        not_applicable.append("critical-roster")
    else:
        required = profile.required_critical_approvers
        declared = profile.critical_ratification_delegates
        resolved_humans = {
            resolved
            for delegate in declared
            if (resolved := policy.resolve_human(delegate))
        }
        if not declared:
            disclosures.append(
                PreflightFinding(
                    "preflight-critical-roster-undeclared",
                    "critical-ratification-delegates",
                    "no delegate roster declared: NO if any Critical surface is "
                    "disturbed (unclassified surfaces default to Critical)",
                )
            )
        elif not resolved_humans:
            disclosures.append(
                PreflightFinding(
                    "preflight-critical-roster-unresolvable",
                    "critical-ratification-delegates",
                    "no declared delegate resolves to an enrolled human: NO if "
                    "any Critical surface is disturbed",
                )
            )
        # 4.1 intake authority-reachability: a roster with ZERO enrolled humans
        # cannot ratify ANY phase, activate any catalog, or approve anything —
        # every authority destination is structurally unreachable, so this is a
        # hard NO at hour zero, not a Critical-scoped disclosure.
        if not policy.human_ids:
            hard_no.append(
                PreflightFinding(
                    "preflight-authority-unreachable",
                    "enrolled-humans",
                    "no enrolled human exists: every required receipt signer is "
                    "unresolvable, so no ratification, activation, or approval "
                    "destination is reachable and __DONE__ is provably unreachable",
                )
            )
        # Computed DIRECTLY from roster size — an emitted insufficient-approvers
        # from a late null probe cannot distinguish expected-empty from
        # impossible; this can (the n=1/I2 collision surfaced at T=0).
        capacity = len({policy.canonical(human) for human in policy.human_ids})
        if capacity < required:
            disclosures.append(
                PreflightFinding(
                    "preflight-critical-approver-capacity",
                    "enrolled-humans",
                    f"{capacity} enrolled human(s) < required Critical approver "
                    f"floor {required}: a Critical surface is permanently "
                    f"unpromotable under this roster — NO if any Critical "
                    f"surface is disturbed",
                )
            )
        else:
            notes.append(
                PreflightFinding(
                    "preflight-critical-approver-capacity-ok",
                    "enrolled-humans",
                    f"{capacity} enrolled human(s) >= floor {required}",
                )
            )

    # --- Phase 1.5 reachability schema joins (additive; undeclared is loud, not green) --
    # Each join is checkable only when BOTH sides are ratified. A plan authored before
    # the additive fields existed has not declared the join — that is "could not check",
    # disclosed per join, never a hard NO against legacy plans and never a silent pass.
    if plan is None or coverage is None:
        not_applicable.append("plan-joins")
    else:
        ratified_verbs = {
            normalize_label(verb) for verb in coverage.verb_ids if normalize_label(verb)
        }
        delivered = {
            normalize_label(verb)
            for step in plan.steps
            for verb in step.delivers_verbs
            if normalize_label(verb)
        }
        if not delivered:
            not_applicable.append("verb-delivery")
            disclosures.append(
                PreflightFinding(
                    "preflight-verb-delivery-undeclared",
                    "verb-delivery",
                    "no step declares delivers_verbs: the verb-to-step join was NOT "
                    "verified — __DONE__ reachability through this plan is unchecked",
                )
            )
        else:
            for verb in sorted(ratified_verbs - delivered):
                hard_no.append(
                    PreflightFinding(
                        "preflight-verb-undeliverable",
                        verb,
                        "ratified verb no step delivers: the handover scope-union can "
                        "never cover it, so __DONE__ is provably unreachable through "
                        "this plan",
                    )
                )
            if ratified_verbs <= delivered:
                notes.append(
                    PreflightFinding(
                        "preflight-verb-delivery-ok",
                        "verb-delivery",
                        f"{len(ratified_verbs)} ratified verb(s) all delivered",
                    )
                )
        required_probes = {
            probe
            for criterion in coverage.adequacy
            for probe in criterion.required_probe_ids
            if str(probe).strip()
        }
        promised = {
            probe
            for step in plan.steps
            for probe in step.promises_probes
            if str(probe).strip()
        }
        if required_probes and not promised:
            not_applicable.append("probe-promises")
            disclosures.append(
                PreflightFinding(
                    "preflight-probe-promises-undeclared",
                    "probe-promises",
                    "adequacy criteria require probes but no step promises any: the "
                    "probe join was NOT verified — receipt adequacy is unchecked",
                )
            )
        elif required_probes:
            for probe in sorted(required_probes - promised):
                hard_no.append(
                    PreflightFinding(
                        "preflight-probe-unpromised",
                        probe,
                        "an adequacy criterion requires this probe but no step "
                        "promises it: the territory can never be receipted, so its "
                        "criterion is unsatisfiable by this plan",
                    )
                )
            if required_probes <= promised:
                notes.append(
                    PreflightFinding(
                        "preflight-probe-promises-ok",
                        "probe-promises",
                        f"{len(required_probes)} required probe(s) all promised",
                    )
                )
        declared_refs = {
            territory.territory_id: tuple(
                ref.strip() for ref in territory.expectation_refs if ref.strip()
            )
            for territory in coverage.territories
            if any(ref.strip() for ref in territory.expectation_refs)
        }
        if not declared_refs:
            not_applicable.append("territory-oracles")
            disclosures.append(
                PreflightFinding(
                    "preflight-territory-oracles-undeclared",
                    "territory-oracles",
                    "no territory declares expectation_refs: the territory-to-oracle "
                    "join was NOT verified",
                )
            )
        else:
            link_keys = {
                f"{link.expectation.artifact_id}:{link.expectation.item_id}"
                for link in plan.oracle_links
            }
            unoracled = False
            for territory_id, refs in sorted(declared_refs.items()):
                for ref in refs:
                    if ref not in link_keys:
                        unoracled = True
                        hard_no.append(
                            PreflightFinding(
                                "preflight-territory-oracle-missing",
                                normalize_label(territory_id),
                                f"declared expectation {ref!r} appears in no oracle "
                                f"link: nothing in this plan can ever clear the "
                                f"territory",
                            )
                        )
            if not unoracled:
                notes.append(
                    PreflightFinding(
                        "preflight-territory-oracles-ok",
                        "territory-oracles",
                        f"{len(declared_refs)} territory declaration(s) all resolve "
                        f"to oracle links",
                    )
                )

    # --- signal-deadline admission (plan 4.1d — the 0.4 mechanical consequence) ---
    if signal_deadline is None:
        not_applicable.append("signal-deadline")
    elif signal_deadline.deadline is None:
        not_applicable.append("signal-deadline")
        disclosures.append(
            PreflightFinding(
                "preflight-signal-deadline-unfrozen",
                "signal-deadline",
                "no frozen deadline exists yet (pre-first-recorded-attempt): the "
                "deadline admission was NOT checked",
            )
        )
    elif signal_deadline.passes >= signal_deadline.deadline:
        if signal_deadline.signal_known and signal_deadline.signal_present:
            notes.append(
                PreflightFinding(
                    "preflight-signal-deadline-satisfied",
                    "signal-deadline",
                    f"pass {signal_deadline.passes}/{signal_deadline.deadline}: a "
                    f"NO-relevant signal exists — the deadline is satisfied",
                )
            )
        elif signal_deadline.blockers_known and not signal_deadline.residual_blockers:
            notes.append(
                PreflightFinding(
                    "preflight-signal-deadline-healthy-exemption",
                    "signal-deadline",
                    f"pass {signal_deadline.passes}/{signal_deadline.deadline} with "
                    f"no residual blockers: a healthy green run that legitimately "
                    f"needs more passes is not a violation (plan 0.4 semantics)",
                )
            )
        elif not signal_deadline.signal_known or not signal_deadline.blockers_known:
            disclosures.append(
                PreflightFinding(
                    "preflight-signal-deadline-unverifiable",
                    "signal-deadline",
                    f"pass {signal_deadline.passes}/{signal_deadline.deadline} but "
                    f"the signal registry or readiness snapshot could not be read: "
                    f"the deadline admission was NOT verified — loud, not green",
                )
            )
        else:
            hard_no.append(
                PreflightFinding(
                    "preflight-signal-deadline-expired",
                    f"pass-{signal_deadline.passes}-of-{signal_deadline.deadline}",
                    "signal deadline expired with no NO-relevant signal and "
                    "residual blockers present: the next BUILDING/model admission "
                    "is refused by the host — never only a pager (plan 0.4/4.1d)",
                )
            )
    else:
        notes.append(
            PreflightFinding(
                "preflight-signal-deadline-within",
                "signal-deadline",
                f"pass {signal_deadline.passes}/{signal_deadline.deadline}",
            )
        )

    # --- dead-run liveness (§1.1: detection only; time predictions are never
    # residual blockers) ----------------------------------------------------------
    if liveness is None:
        not_applicable.append("run-liveness")
    else:
        if liveness.ledger_error:
            hard_no.append(
                PreflightFinding(
                    "preflight-dead-run-ledger-unloadable",
                    "run-ledger",
                    f"the run ledger refuses to load: {liveness.ledger_error}",
                )
            )
        if (
            liveness.state == "blocked"
            and liveness.build_attempt_limit > 0
            and liveness.build_attempt_count >= liveness.build_attempt_limit
        ):
            hard_no.append(
                PreflightFinding(
                    "preflight-dead-run-blocked-at-ceiling",
                    "attempts",
                    f"BLOCKED with {liveness.build_attempt_count}/"
                    f"{liveness.build_attempt_limit} attempts spent: no attempt "
                    f"remains — repair authorization or terminal NO, never more "
                    f"construction",
                )
            )
        for guard in liveness.guard_residue:
            hard_no.append(
                PreflightFinding(
                    "preflight-dead-run-guard-residue",
                    guard,
                    "a stale exclusive guard blocks every transition (interrupted "
                    "action evidence); release requires the receipted ceremony, "
                    "not construction",
                )
            )
        if liveness.chain_error:
            hard_no.append(
                PreflightFinding(
                    "preflight-dead-run-chain-unloadable",
                    "harness-receipt-chain",
                    f"the harness receipt chain refuses to load — promotion is "
                    f"permanently refused until the repair ceremony: "
                    f"{liveness.chain_error}",
                )
            )
        if not (
            liveness.ledger_error
            or liveness.chain_error
            or liveness.guard_residue
            or liveness.state == "blocked"
        ):
            notes.append(
                PreflightFinding(
                    "preflight-run-live",
                    "run-liveness",
                    f"state {liveness.state or '(new)'} with "
                    f"{liveness.build_attempt_count}/"
                    f"{liveness.build_attempt_limit or '-'} attempts spent",
                )
            )

    if hard_no:
        ceiling = VERDICT_BLOCK
    return PreflightReport(
        go=not hard_no,
        ceiling=ceiling,
        hard_no=tuple(hard_no),
        disclosures=tuple(disclosures),
        notes=tuple(notes),
        not_applicable=tuple(not_applicable),
    )


def probe_liveness(runs_root: str | Any, run_id: str) -> LivenessFacts:
    """Filesystem prober for the liveness leg — the module's one impure seam.

    Reads the verified projection (a refusing ledger IS the finding, never an
    exception), the two exclusive-guard residues that wedge transitions, and
    the harness receipt chain whose R5-class wedge refuses all promotion.
    """

    from pathlib import Path as _Path

    from factory_runtime.promotion_gate import PromotionGateError, _chain_path, _load_chain
    from factory_runtime.state import RunStateError, RunStore

    root = _Path(str(runs_root))
    run_root = root / run_id
    state = ""
    count = 0
    limit = 0
    ledger_error = ""
    try:
        projection = RunStore(root).load(run_id)
        state = projection.state
        count = projection.build_attempt_count
        limit = projection.build_attempt_limit
    except RunStateError as exc:
        ledger_error = str(exc)[:300]
    guards = tuple(
        str(candidate)
        for candidate in (
            run_root / "ledger.jsonl.lock",
            run_root / "resources.guard",
            run_root / "run-transition.guard",
        )
        if candidate.exists()
    )
    chain_error = ""
    chain = _chain_path(run_root)
    if chain.exists():
        try:
            _load_chain(chain)
        except PromotionGateError as exc:
            chain_error = str(exc)[:300]
    return LivenessFacts(
        state=state,
        build_attempt_count=count,
        build_attempt_limit=limit,
        guard_residue=guards,
        ledger_error=ledger_error,
        chain_error=chain_error,
    )


def probe_signal_deadline(
    runs_root: str | Any, run_id: str, harness_dir: str | Any | None = None
) -> SignalDeadlineFacts:
    """Filesystem prober for the signal-deadline admission (impure seam, 4.1d).

    Passes come from the verified run ledger; the deadline from the frozen
    generation blob; NO-relevance from the committed baseline mirror the
    watchdog also consumes (the registries own the fact; the baseline cites
    it, contradiction-checked by check_acceptance); residual blockers from the
    retained generation-readiness snapshot. Every unreadable input degrades to
    its known=False tri-state — could-not-check is loud downstream, never green.
    """

    import json as _json
    from pathlib import Path as _Path

    from factory_runtime.generation import _frozen_signal_deadline, _generation_blob
    from factory_runtime.state import RunStateError, RunStore

    root = _Path(str(runs_root))
    passes = 0
    deadline: int | None = None
    blockers_known = False
    residual: tuple[str, ...] = ()
    try:
        store = RunStore(root)
        projection = store.load(run_id)
        passes = store.validating_pass_count(run_id)
        deadline = _frozen_signal_deadline(root, run_id, projection)
        readiness_digest = dict(projection.generation_artifact_digests).get(
            "generation-readiness", ""
        )
        if readiness_digest:
            blob = _generation_blob(root, run_id, "generation-readiness", readiness_digest)
            document = _json.loads(blob.payload_path.read_text(encoding="utf-8"))
            residual = tuple(
                str(issue) for issue in (document.get("report") or {}).get("issues", [])
            )
            blockers_known = True
    except (RunStateError, OSError, ValueError) as _exc:  # noqa: F841
        deadline = None

    signal_known = False
    signal_present = False
    harness = (
        _Path(str(harness_dir))
        if harness_dir is not None
        else _Path(__file__).resolve().parent.parent / "harness"
    )
    baseline_path = harness.parent / "acceptance_baseline.json"
    try:
        baseline = _json.loads(baseline_path.read_text(encoding="utf-8"))
        kinds = baseline.get("no_relevant_kinds")
        if isinstance(kinds, dict):
            relevant = {kind for kind, flag in kinds.items() if flag is True}
            signal_known = True
            events_path = root / run_id / "events.jsonl"
            try:
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    try:
                        row = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if isinstance(row, dict) and row.get("kind") in relevant:
                        signal_present = True
                        break
            except OSError:
                signal_present = False  # no events file: genuinely no signal yet
    except (OSError, _json.JSONDecodeError):
        signal_known = False

    return SignalDeadlineFacts(
        passes=passes,
        deadline=deadline,
        signal_known=signal_known,
        signal_present=signal_present,
        blockers_known=blockers_known,
        residual_blockers=residual,
    )
