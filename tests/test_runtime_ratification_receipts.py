"""A `*-ratified` transition must carry its two receipts at the store, not only in the workflow.

`WorkflowEngine.ratify_phase` verifies a human receipt and a distinct enrolled non-human Validator
receipt through `verify_receipt`, then records both envelope digests. That verification is real, and
it was also the *only* thing standing behind the requirement: `RunStore.transition` asked for the
phase artifact digest and nothing else, so anything holding a store reached a ratified state with
`artifact_digests={phase: digest}`. A control that lives only in the workflow layer is bypassable by
anything holding a store — the same reasoning that put the anchor-state controls at the store.

The store cannot verify a signature and must not try: that is Tessera's job, behind a seam, and
`manifest.py` deliberately holds no key material. What the store CAN do is refuse to record a
ratification that does not name two distinct receipts over the artifact. That is the difference
between "receipts were verified somewhere" and "this entry is inadmissible without them."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory_core.manifest import LedgerEntry
from factory_runtime.state import RunState, RunStateError, RunStore
from tests.conftest import ratification_receipts

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
PRODUCT = "sha256:" + ("3" * 64)
ARCHITECTURE = "sha256:" + ("4" * 64)
OPERATIONS = "sha256:" + ("5" * 64)
HUMAN_RECEIPT = "sha256:" + ("a" * 64)
VALIDATOR_RECEIPT = "sha256:" + ("b" * 64)
OTHER_RECEIPT = "sha256:" + ("c" * 64)
AMENDED = "sha256:" + ("6" * 64)


class _Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _receipts(phase: str, *, human: str = "", validator: str = ""):
    """Per-phase receipt digests. Distinct per phase, because a receipt binds to one subject and
    the store refuses a digest already recorded in the run."""
    return {
        f"{phase}:human-receipt": human or ratification_receipts(phase)[f"{phase}:human-receipt"],
        f"{phase}:validator-receipt": (
            validator or ratification_receipts(phase)[f"{phase}:validator-receipt"]
        ),
    }


def _store(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path, clock=_Clock())
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    return store


def _ratify(store: RunStore, **overrides: str) -> None:
    digests = {"product-specification": PRODUCT, **_receipts("product-specification")}
    digests.update(overrides)
    store.transition(
        "run-1",
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
        actor="validator",
        artifact_digests=digests,
    )


def test_ratification_with_both_receipts_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ratify(store)
    assert store.load("run-1").state == RunState.PRODUCT_SPECIFICATION_RATIFIED


def test_ratification_without_receipts_is_refused(tmp_path: Path) -> None:
    """The exact bypass: the artifact digest alone used to be enough."""
    store = _store(tmp_path)
    with pytest.raises(RunStateError, match="human-receipt"):
        store.transition(
            "run-1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
            artifact_digests={"product-specification": PRODUCT},
        )


def test_ratification_without_the_validator_receipt_is_refused(tmp_path: Path) -> None:
    """A human approval on its own is not a ratification; the attestation is independent."""
    store = _store(tmp_path)
    with pytest.raises(RunStateError, match="validator-receipt"):
        store.transition(
            "run-1",
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            actor="validator",
            artifact_digests={
                "product-specification": PRODUCT,
                "product-specification:human-receipt": HUMAN_RECEIPT,
            },
        )


def test_one_receipt_cited_twice_is_refused(tmp_path: Path) -> None:
    """Two names for one envelope is one receipt. `ratify_phase` already refuses identical
    ratifier identities; this refuses the same collapse expressed as digests."""
    store = _store(tmp_path)
    with pytest.raises(RunStateError, match="distinct"):
        _ratify(
            store,
            **{
                "product-specification:human-receipt": HUMAN_RECEIPT,
                "product-specification:validator-receipt": HUMAN_RECEIPT,
            },
        )


def test_a_receipt_digest_equal_to_the_artifact_digest_is_refused(tmp_path: Path) -> None:
    """A receipt envelope contains a signature over the artifact, so it cannot hash to it.
    Equality means the caller padded the map to satisfy the check."""
    store = _store(tmp_path)
    with pytest.raises(RunStateError, match="distinct"):
        _ratify(store, **{"product-specification:human-receipt": PRODUCT})


def test_each_phase_requires_its_own_receipts(tmp_path: Path) -> None:
    """The keys are phase-scoped, so a later phase cannot ride on an earlier phase's receipts."""
    store = _store(tmp_path)
    _ratify(store)
    with pytest.raises(RunStateError, match="architecture:human-receipt"):
        store.transition(
            "run-1",
            RunState.ARCHITECTURE_RATIFIED,
            actor="validator",
            artifact_digests={"architecture": ARCHITECTURE},
        )


def test_every_ratified_state_is_covered(tmp_path: Path) -> None:
    """Not just the first phase. Walks all three, each supplying its own pair."""
    store = _store(tmp_path)
    for state, key, digest in (
        (RunState.PRODUCT_SPECIFICATION_RATIFIED, "product-specification", PRODUCT),
        (RunState.ARCHITECTURE_RATIFIED, "architecture", ARCHITECTURE),
        (RunState.OPERATIONAL_MATURITY_RATIFIED, "operational-maturity", OPERATIONS),
    ):
        with pytest.raises(RunStateError, match=f"{key}:human-receipt"):
            store.transition("run-1", state, actor="validator", artifact_digests={key: digest})
        store.transition(
            "run-1",
            state,
            actor="validator",
            artifact_digests={key: digest, **_receipts(key)},
        )
    assert store.load("run-1").state == RunState.OPERATIONAL_MATURITY_RATIFIED


def test_derive_refuses_a_ratification_entry_appended_without_receipts(tmp_path: Path) -> None:
    """`_derive` is the authority, so it must refuse what `transition` refuses.

    The hash chain catches an *edited* entry. It does not catch one appended through the ledger
    directly, which chains validly and would otherwise project as a legitimate ratification.
    """
    store = _store(tmp_path)
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.INTAKE,
            to_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            artifact_digests={
                "product-specification": PRODUCT,  # no receipts: transition() would refuse this
                "target": TARGET,
                "source": SOURCE,
                "phase_artifacts": {"product-specification": PRODUCT},
            },
            actor="validator",
            created_at="101",
        )
    )
    with pytest.raises(RunStateError, match="human-receipt"):
        store.load("run-1")


def test_derive_refuses_a_ratification_entry_whose_receipts_collapse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.INTAKE,
            to_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            artifact_digests={
                "product-specification": PRODUCT,
                "product-specification:human-receipt": OTHER_RECEIPT,
                "product-specification:validator-receipt": OTHER_RECEIPT,
                "target": TARGET,
                "source": SOURCE,
                "phase_artifacts": {"product-specification": PRODUCT},
            },
            actor="validator",
            created_at="101",
        )
    )
    with pytest.raises(RunStateError, match="distinct"):
        store.load("run-1")


def test_a_receipt_cannot_ratify_a_second_version_of_its_phase(tmp_path: Path) -> None:
    """After a `specification-defect`, re-ratifying with the same receipts is refused.

    A receipt binds to one subject digest, so a receipt over the bytes the defect just invalidated
    cannot ratify their replacement. Doctrine already says a new signed version invalidates
    downstream work derived from the old digest; this is that, enforced where the re-ratification
    is written.
    """
    store = _store(tmp_path)
    _ratify(store)
    store.transition(
        "run-1",
        RunState.SPECIFICATION_DEFECT,
        actor="validator",
        payload={"phase": "product-specification"},
    )
    with pytest.raises(RunStateError, match="reuses receipt digest"):
        _ratify(store, **{"product-specification": AMENDED})

    store.transition(
        "run-1",
        RunState.PRODUCT_SPECIFICATION_RATIFIED,
        actor="validator",
        artifact_digests={
            "product-specification": AMENDED,
            **_receipts("product-specification", human=HUMAN_RECEIPT, validator=VALIDATOR_RECEIPT),
        },
    )
    assert store.load("run-1").phase_artifact_digests["product-specification"] == AMENDED


def test_a_receipt_cannot_be_reused_across_phases(tmp_path: Path) -> None:
    """The same envelope cannot ratify product-specification and then architecture."""
    store = _store(tmp_path)
    _ratify(store)
    reused = _receipts("product-specification")["product-specification:human-receipt"]
    with pytest.raises(RunStateError, match="reuses receipt digest"):
        store.transition(
            "run-1",
            RunState.ARCHITECTURE_RATIFIED,
            actor="validator",
            artifact_digests={
                "architecture": ARCHITECTURE,
                **_receipts("architecture", human=reused),
            },
        )


def test_derive_refuses_a_ledger_that_reuses_a_receipt(tmp_path: Path) -> None:
    """`_derive` accumulates receipts across the whole chain, not just within one entry."""
    store = _store(tmp_path)
    _ratify(store)
    reused = _receipts("product-specification")
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            to_state=RunState.ARCHITECTURE_RATIFIED,
            artifact_digests={
                "architecture": ARCHITECTURE,
                "architecture:human-receipt": reused["product-specification:human-receipt"],
                "architecture:validator-receipt": reused["product-specification:validator-receipt"],
                "target": TARGET,
                "source": SOURCE,
                "phase_artifacts": {
                    "product-specification": PRODUCT,
                    "architecture": ARCHITECTURE,
                },
            },
            actor="validator",
            created_at="103",
        )
    )
    with pytest.raises(RunStateError, match="reuses receipt digest"):
        store.load("run-1")


def test_derive_refuses_a_ratification_entry_that_omits_the_artifact_digest(
    tmp_path: Path,
) -> None:
    """The helper requires the phase digest itself, not just the receipts.

    `transition` checks it before calling, but `_derive` reads a map an appender controls. Without
    this, an entry could omit the top-level digest and reach the distinctness check with an empty
    artifact value — which is what makes "no receipt equals the artifact digest" mean anything.
    """
    store = _store(tmp_path)
    receipts = _receipts("product-specification")
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.INTAKE,
            to_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            artifact_digests={
                # no top-level "product-specification": only the phase map carries it
                **receipts,
                "target": TARGET,
                "source": SOURCE,
                "phase_artifacts": {"product-specification": PRODUCT},
            },
            actor="validator",
            created_at="101",
        )
    )
    with pytest.raises(RunStateError, match="requires artifact digest"):
        store.load("run-1")


def test_derive_refuses_an_entry_that_disagrees_with_itself_about_the_artifact(
    tmp_path: Path,
) -> None:
    """The ratified digest is recorded twice; two records that disagree are inadmissible."""
    store = _store(tmp_path)
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.INTAKE,
            to_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            artifact_digests={
                "product-specification": PRODUCT,
                **_receipts("product-specification"),
                "target": TARGET,
                "source": SOURCE,
                "phase_artifacts": {"product-specification": AMENDED},
            },
            actor="validator",
            created_at="101",
        )
    )
    with pytest.raises(RunStateError, match="disagrees with itself"):
        store.load("run-1")


def test_a_whitespace_padded_receipt_digest_is_refused(tmp_path: Path) -> None:
    """Validated unstripped: a padded value is not the value recorded in the entry."""
    store = _store(tmp_path)
    padded = " " + HUMAN_RECEIPT + " "
    store._ledger("run-1").append(
        LedgerEntry(
            capability_id="run-1",
            from_state=RunState.INTAKE,
            to_state=RunState.PRODUCT_SPECIFICATION_RATIFIED,
            artifact_digests={
                "product-specification": PRODUCT,
                "product-specification:human-receipt": padded,
                "product-specification:validator-receipt": VALIDATOR_RECEIPT,
                "target": TARGET,
                "source": SOURCE,
                "phase_artifacts": {"product-specification": PRODUCT},
            },
            actor="validator",
            created_at="101",
        )
    )
    with pytest.raises(RunStateError, match="canonical sha256 digest"):
        store.load("run-1")
