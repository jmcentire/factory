"""The two anchor transitions past `preview` must carry authority, not just an actor string.

Before these controls, `human-approved` and `promoted` required a non-empty `actor` and nothing
else: no artifact digest, no distinct approver, and no binding between what a human
approved and what was ultimately promoted. (`ci` sits between them and carries no authority
requirement of its own; it gains none here.) That made slice 5's proof condition — "the artifact
shown to the human is byte-for-byte the artifact promoted" — unenforceable, because nothing
recorded which artifact the human saw.

These tests are the enforcement. They exercise `RunStore.transition` directly, which is the
lowest level the guarantee has to hold at: a control that only lives in the workflow layer can be
bypassed by anything holding a store.

This is a floor, NOT the destination. Per issue #4 (2026-08-05) the intended shape for these
transitions is a signed move record — prior-state hash, operation, new-state hash, actor key, and
a signature over all four — verified by re-derivation rather than by trusting a declared outcome,
with receipt verification against payload hash and required capabilities living in the core. An
artifact digest plus two present-and-distinct identity strings is what can be enforced today; it
is deliberately weaker than that, and it does not foreclose it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory_core.manifest import LedgerEntry
from factory_runtime.resources import ResourceLedger
from factory_runtime.state import _ANCHOR_STATE_KEYS, RunState, RunStateError, RunStore
from tests.conftest import (
    acceptance_catalog_artifacts,
    build_payload,
    ci_artifacts,
    create_intake_run,
    generation_artifacts,
    preview_artifacts,
    ratification_receipts,
    synthetic_candidate_digest,
    terminalize_run_resources,
    validation_artifacts,
)

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
PRODUCT = "sha256:" + ("3" * 64)
ARCHITECTURE = "sha256:" + ("4" * 64)
OPERATIONS = "sha256:" + ("5" * 64)
CANDIDATE = synthetic_candidate_digest()
OTHER_CANDIDATE = "sha256:" + ("b" * 64)


class _Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _run_at_preview(tmp_path: Path) -> RunStore:
    """Drive a run to `preview`, which is as far as the machinery reached before slice 5."""
    store = RunStore(tmp_path, clock=_Clock())
    create_intake_run(
        store,
        run_id="run-1",
        target_digest=TARGET,
        source_digest=SOURCE,
    )
    for state, key, digest in (
        (RunState.PRODUCT_SPECIFICATION_RATIFIED, "product-specification", PRODUCT),
        (RunState.ARCHITECTURE_RATIFIED, "architecture", ARCHITECTURE),
        (RunState.OPERATIONAL_MATURITY_RATIFIED, "operational-maturity", OPERATIONS),
    ):
        store.transition(
            "run-1",
            state,
            actor="validator",
            artifact_digests={key: digest, **ratification_receipts(key)},
        )
    store.transition(
        "run-1",
        RunState.BUILDING,
        actor="validator",
        artifact_digests={
            **generation_artifacts(include_acceptance_catalog=False),
            **acceptance_catalog_artifacts(store),
        },
        payload=build_payload(),
    )
    store.transition(
        "run-1",
        RunState.VALIDATING,
        actor="validator",
        artifact_digests=validation_artifacts(candidate=CANDIDATE),
        payload={"tester_identity": "tester"},
        implementer_identity="coder",
    )
    store.transition(
        "run-1",
        RunState.PREVIEW,
        actor="validator",
        artifact_digests=preview_artifacts(store, candidate=CANDIDATE),
        verifier_identity="validator",
    )
    return store


def _approve(store: RunStore, *, candidate: str = CANDIDATE, **kwargs: str) -> None:
    store.transition(
        "run-1",
        RunState.HUMAN_APPROVED,
        actor=kwargs.pop("actor", "validator"),
        artifact_digests={"candidate": candidate},
        approver_identity=kwargs.pop("approver_identity", "human-approver"),
        implementer_identity=kwargs.pop("implementer_identity", "coder"),
        **kwargs,
    )


def test_human_approval_without_a_candidate_digest_is_refused(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    with pytest.raises(RunStateError, match="candidate"):
        store.transition(
            "run-1",
            RunState.HUMAN_APPROVED,
            actor="validator",
            approver_identity="human-approver",
        )


def test_human_approval_without_an_approver_identity_is_refused(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    with pytest.raises(RunStateError, match="approver"):
        store.transition(
            "run-1",
            RunState.HUMAN_APPROVED,
            actor="validator",
            artifact_digests={"candidate": CANDIDATE},
        )


def test_human_approval_by_the_implementer_is_refused(tmp_path: Path) -> None:
    """This is also the n=1 bootstrap case, and it is the open question of issue #4.

    A lone human who implements and approves lands here. The refusal is what I2 says and what
    ``LedgerEntry.validate_sod`` enforces in the core; it is NOT a rule this control invented.
    But the founder's 2026-08-05 answer holds that n=1 is legitimate and current, so this test
    is the single place that changes if I2 is amended or a recorded collapse is introduced.
    Before these controls, this case "passed" by omitting the implementer entirely — see
    ``test_human_approval_without_an_implementer_identity_is_refused``.
    """
    store = _run_at_preview(tmp_path)
    with pytest.raises(RunStateError, match="distinct"):
        _approve(store, approver_identity="coder", implementer_identity="coder")


def test_promoting_a_different_digest_than_was_approved_is_refused(tmp_path: Path) -> None:
    """The byte-for-byte property: promote what the human approved, or refuse."""
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())

    with pytest.raises(RunStateError, match="approved candidate"):
        store.transition(
            "run-1",
            RunState.PROMOTED,
            actor="validator",
            artifact_digests={"promoted-artifact": OTHER_CANDIDATE},
        )


def test_promoting_without_naming_the_artifact_is_refused(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    _approve(store)
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())

    with pytest.raises(RunStateError, match="promoted-artifact"):
        store.transition("run-1", RunState.PROMOTED, actor="validator")


def test_promoting_the_approved_candidate_succeeds_and_is_resumable(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())
    terminalize_run_resources(store, run_id="run-1")
    projection = store.transition(
        "run-1",
        RunState.PROMOTED,
        actor="validator",
        artifact_digests={"promoted-artifact": CANDIDATE},
    )

    assert projection.state == RunState.PROMOTED
    reloaded = RunStore(tmp_path, clock=_Clock()).load("run-1")
    assert reloaded.state == RunState.PROMOTED
    assert reloaded.approved_candidate_digest == CANDIDATE
    latest = store.current_artifact_digests("run-1")
    assert latest["resource-ledger"].startswith("sha256:")
    assert latest["resource-ledger-seal"].startswith("sha256:")


def test_direct_promoted_ledger_append_cannot_bypass_resource_close(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())
    ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100).append(
        generation=1,
        resource_id="unfinished-workspace",
        resource_type="lane-workspace",
        identifier=str(tmp_path / "run-1" / "workspaces" / "coder"),
        creator_action="test",
        ownership="run-owned",
        baseline={"absent_at_plan": True},
        disposition={},
        status="planned",
        evidence_digests={},
        actor="test",
    )
    artifacts = dict(store.current_artifact_digests("run-1"))
    artifacts.update(
        {
            "promoted-artifact": CANDIDATE,
            "resource-ledger": "sha256:" + ("c" * 64),
            "resource-ledger-seal": "sha256:" + ("d" * 64),
        }
    )
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.CI,
            to_state=RunState.PROMOTED,
            artifact_digests=artifacts,
            payload={},
            actor="route-around",
            created_at="101",
        )
    )

    with pytest.raises(RunStateError, match="terminal resource seal"):
        store.rebuild_projection("run-1")


def test_crash_after_resource_seal_is_a_promotion_only_resumable_state(
    tmp_path: Path,
) -> None:
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)
    store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())
    terminalize_run_resources(store, run_id="run-1")
    ResourceLedger(tmp_path / "run-1", "run-1", clock=lambda: 100).seal_for_close(actor="gate-l")

    assert store.load("run-1").state == RunState.CI
    with pytest.raises(RunStateError, match="only an idempotent promotion retry"):
        store.transition(
            "run-1", RunState.BLOCKED, actor="validator", payload={"reason": "test-block"}
        )

    promoted = store.transition(
        "run-1",
        RunState.PROMOTED,
        actor="gate-l",
        artifact_digests={"promoted-artifact": CANDIDATE},
    )
    assert promoted.state == RunState.PROMOTED


def test_specification_defect_invalidates_prior_candidate_approval(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)

    projection = store.transition(
        "run-1",
        RunState.SPECIFICATION_DEFECT,
        actor="validator",
        payload={"phase": "product-specification"},
    )

    assert projection.approved_candidate_digest == ""
    assert RunStore(tmp_path, clock=_Clock()).load("run-1").approved_candidate_digest == ""


def test_human_approval_without_an_implementer_identity_is_refused(tmp_path: Path) -> None:
    """A distinctness check against an empty implementer proves nothing.

    `LedgerEntry` enforces distinctness only among identities actually present, which is the
    right general default. `human-approved` is a state where the implementer IS a required
    signer: without one recorded, "approved by someone other than whoever built it" is
    unverifiable, so it fails closed rather than passing vacuously.
    """
    store = _run_at_preview(tmp_path)
    with pytest.raises(RunStateError, match="implementer"):
        store.transition(
            "run-1",
            RunState.HUMAN_APPROVED,
            actor="validator",
            artifact_digests={"candidate": CANDIDATE},
            approver_identity="human-approver",
        )


def test_derive_refuses_an_approval_entry_with_collapsed_identities(tmp_path: Path) -> None:
    """`_derive` is the authority, so it must re-check what `transition` refuses.

    The hash chain already catches an *edited* entry. This covers the other route: an entry
    appended through the ledger directly, bypassing `transition`, which chains validly and so
    would otherwise project as a legitimate approval.
    """
    from factory_core.manifest import LedgerEntry

    store = _run_at_preview(tmp_path)
    current = store.load("run-1")
    generation = dict(current.generation_artifact_digests)
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.PREVIEW,
            to_state=RunState.HUMAN_APPROVED,
            implementer_identity="coder",
            approver_identity="",  # no approver: transition() would refuse this
            artifact_digests={
                "candidate": CANDIDATE,
                "target": TARGET,
                "target-state": current.target_state_digest,
                "source": SOURCE,
                "acceptance_obligation_catalog": (current.acceptance_obligation_catalog_digest),
                "phase_artifacts": {
                    "product-specification": PRODUCT,
                    "architecture": ARCHITECTURE,
                    "operational-maturity": OPERATIONS,
                },
                "generation_artifacts": generation,
            },
            payload={},
            actor="validator",
            created_at="200",
        ),
        None,
    )

    with pytest.raises(RunStateError, match="approver"):
        store.rebuild_projection("run-1")


@pytest.mark.parametrize("anchor_state", sorted(_ANCHOR_STATE_KEYS, key=str))
def test_derive_requires_every_anchor_digest_the_write_path_requires(
    tmp_path: Path, anchor_state: RunState
) -> None:
    """Driven off `_ANCHOR_STATE_KEYS` so the table, not this file, decides the coverage.

    `transition` enforces the anchor digest generically from that table; `_derive` used to name
    the two states individually.  Both had the same two entries, so nothing was unenforced -- but
    a third entry would have been enforced on the write path and skipped by `_derive`, and
    `_derive` is the authority.  Parametrizing off the table means adding an anchor state without
    covering `_derive` fails here rather than shipping a one-sided control.
    """
    from factory_core.manifest import LedgerEntry

    store = _run_at_preview(tmp_path)
    anchor_key = _ANCHOR_STATE_KEYS[anchor_state]
    current = store.load("run-1")
    generation = dict(current.generation_artifact_digests)
    phase_artifacts = {
        "product-specification": PRODUCT,
        "architecture": ARCHITECTURE,
        "operational-maturity": OPERATIONS,
    }
    # Walk to the state before the one under test, then append its entry with the anchor digest
    # omitted -- the one thing `transition` would have refused.
    prior = RunState.PREVIEW
    if anchor_state is not RunState.HUMAN_APPROVED:
        _approve(store)
        store.transition("run-1", RunState.CI, actor="validator", artifact_digests=ci_artifacts())
        prior = RunState.CI
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=prior,
            to_state=anchor_state,
            implementer_identity="coder",
            approver_identity="human-approver",
            artifact_digests={
                # no `anchor_key`: that is the omission under test
                "target": TARGET,
                "target-state": current.target_state_digest,
                "source": SOURCE,
                "acceptance_obligation_catalog": (current.acceptance_obligation_catalog_digest),
                "phase_artifacts": phase_artifacts,
                "generation_artifacts": generation,
            },
            payload={},
            actor="validator",
            created_at="300",
        ),
        None,
    )

    with pytest.raises(RunStateError, match=f"{anchor_key} digest"):
        store.rebuild_projection("run-1")
