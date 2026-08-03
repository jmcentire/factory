"""The three anchor transitions past `preview` must carry authority, not just an actor string.

Before these controls, `human-approved`, `ci`, and `promoted` required a non-empty `actor` and
nothing else: no artifact digest, no distinct approver, and no binding between what a human
approved and what was ultimately promoted. That made slice 5's proof condition — "the artifact
shown to the human is byte-for-byte the artifact promoted" — unenforceable, because nothing
recorded which artifact the human saw.

These tests are the enforcement. They exercise `RunStore.transition` directly, which is the
lowest level the guarantee has to hold at: a control that only lives in the workflow layer can be
bypassed by anything holding a store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory_runtime.state import RunState, RunStateError, RunStore

TARGET = "sha256:" + ("1" * 64)
SOURCE = "sha256:" + ("2" * 64)
PRODUCT = "sha256:" + ("3" * 64)
ARCHITECTURE = "sha256:" + ("4" * 64)
OPERATIONS = "sha256:" + ("5" * 64)
CANDIDATE = "sha256:" + ("a" * 64)
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
    store.create("run-1", target_digest=TARGET, source_digest=SOURCE, actor="validator")
    for state, key, digest in (
        (RunState.PRODUCT_SPECIFICATION_RATIFIED, "product-specification", PRODUCT),
        (RunState.ARCHITECTURE_RATIFIED, "architecture", ARCHITECTURE),
        (RunState.OPERATIONAL_MATURITY_RATIFIED, "operational-maturity", OPERATIONS),
    ):
        store.transition("run-1", state, actor="validator", artifact_digests={key: digest})
    store.transition("run-1", RunState.BUILDING, actor="validator")
    store.transition("run-1", RunState.VALIDATING, actor="validator")
    store.transition("run-1", RunState.PREVIEW, actor="validator")
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
    store = _run_at_preview(tmp_path)
    with pytest.raises(RunStateError, match="distinct"):
        _approve(store, approver_identity="coder", implementer_identity="coder")


def test_promoting_a_different_digest_than_was_approved_is_refused(tmp_path: Path) -> None:
    """The byte-for-byte property: promote what the human approved, or refuse."""
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)
    store.transition("run-1", RunState.CI, actor="validator")

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
    store.transition("run-1", RunState.CI, actor="validator")

    with pytest.raises(RunStateError, match="promoted-artifact"):
        store.transition("run-1", RunState.PROMOTED, actor="validator")


def test_promoting_the_approved_candidate_succeeds_and_is_resumable(tmp_path: Path) -> None:
    store = _run_at_preview(tmp_path)
    _approve(store, candidate=CANDIDATE)
    store.transition("run-1", RunState.CI, actor="validator")
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
