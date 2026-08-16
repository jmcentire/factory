"""Persisted, fail-closed Factory run state.

The hash-chained :class:`factory_core.manifest.Ledger` is authoritative. ``run.json`` is only
a projection for convenient reads and is accepted only when it exactly matches a freshly
re-derived ledger view. This keeps a stale or edited status file from becoming authority.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from factory_core.manifest import (
    Ledger,
    LedgerEntry,
    LedgerIntegrityError,
    SegregationPolicy,
    digest_obj,
)
from factory_runtime.resources import ResourceLedger, ResourceLedgerError
from factory_runtime.schema import DocumentValidationError, validate_document
from factory_runtime.transition_obligations import (
    REPORT_KEY as TRANSITION_OBLIGATION_REPORT_KEY,
)
from factory_runtime.transition_obligations import (
    SET_KEY as TRANSITION_OBLIGATION_SET_KEY,
)
from factory_runtime.transition_obligations import (
    TransitionObligationError,
    assert_catalog_covers,
    derive_transition_obligations,
    retain_transition_obligations,
    verify_retained_transition_obligations,
)

RUN_SCHEMA_VERSION = "factory-run/4"
LEGACY_RUN_SCHEMA_VERSIONS = frozenset({"factory-run/1", "factory-run/2", "factory-run/3"})
TARGET_STATE_RUN_SCHEMA_VERSIONS = frozenset({"factory-run/3", RUN_SCHEMA_VERSION})
GENERATION_RUN_SCHEMA_VERSIONS = frozenset({"factory-run/2", "factory-run/3", RUN_SCHEMA_VERSION})
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

GENERATION_ARTIFACT_KEYS: tuple[str, ...] = (
    "target-manifest-source",
    "pattern-catalog",
    "pattern-catalog-source",
    "build-plan",
    "build-plan-source",
    "build-input",
    "generation-readiness",
)

ACCEPTANCE_OBLIGATION_CATALOG_KEY = "acceptance-obligation-catalog"
ACCEPTANCE_OBLIGATION_CATALOG_STRUCTURAL_KEY = "acceptance_obligation_catalog"
ACCEPTANCE_OBLIGATION_REPORT_KEY = "acceptance-obligation-report"
TEST_CHANGE_AUTHORIZATION_KEY = "test-change-authorization"


class RunState(StrEnum):
    """The only states an executable Factory run may occupy."""

    TARGET_RESOLUTION_AUTHORIZED = "target-resolution-authorized"
    TARGET_RESOLVED = "target-resolved"
    INTAKE = "intake"
    PRODUCT_SPECIFICATION_RATIFIED = "product-specification-ratified"
    ARCHITECTURE_RATIFIED = "architecture-ratified"
    OPERATIONAL_MATURITY_RATIFIED = "operational-maturity-ratified"
    BUILDING = "building"
    VALIDATING = "validating"
    PREVIEW = "preview"
    HUMAN_APPROVED = "human-approved"
    CI = "ci"
    PROMOTED = "promoted"
    SPECIFICATION_DEFECT = "specification-defect"
    BLOCKED = "blocked"


ALLOWED_TRANSITIONS: Mapping[RunState, frozenset[RunState]] = {
    RunState.TARGET_RESOLUTION_AUTHORIZED: frozenset({RunState.TARGET_RESOLVED}),
    RunState.TARGET_RESOLVED: frozenset({RunState.INTAKE}),
    RunState.INTAKE: frozenset({RunState.PRODUCT_SPECIFICATION_RATIFIED}),
    RunState.PRODUCT_SPECIFICATION_RATIFIED: frozenset(
        {
            RunState.ARCHITECTURE_RATIFIED,
            RunState.SPECIFICATION_DEFECT,
        }
    ),
    RunState.ARCHITECTURE_RATIFIED: frozenset(
        {
            RunState.OPERATIONAL_MATURITY_RATIFIED,
            RunState.SPECIFICATION_DEFECT,
        }
    ),
    RunState.OPERATIONAL_MATURITY_RATIFIED: frozenset(
        {
            RunState.BUILDING,
            RunState.SPECIFICATION_DEFECT,
        }
    ),
    RunState.BUILDING: frozenset(
        {
            RunState.VALIDATING,
            RunState.SPECIFICATION_DEFECT,
            RunState.BLOCKED,
        }
    ),
    RunState.VALIDATING: frozenset(
        {
            RunState.BUILDING,
            RunState.PREVIEW,
            RunState.SPECIFICATION_DEFECT,
            RunState.BLOCKED,
        }
    ),
    RunState.PREVIEW: frozenset(
        {
            RunState.HUMAN_APPROVED,
            RunState.SPECIFICATION_DEFECT,
            RunState.BLOCKED,
        }
    ),
    RunState.HUMAN_APPROVED: frozenset(
        {
            RunState.CI,
            RunState.SPECIFICATION_DEFECT,
            RunState.BLOCKED,
        }
    ),
    RunState.CI: frozenset(
        {
            RunState.PROMOTED,
            RunState.SPECIFICATION_DEFECT,
            RunState.BLOCKED,
        }
    ),
    RunState.PROMOTED: frozenset(),
    RunState.SPECIFICATION_DEFECT: frozenset(
        {
            RunState.PRODUCT_SPECIFICATION_RATIFIED,
            RunState.ARCHITECTURE_RATIFIED,
            RunState.OPERATIONAL_MATURITY_RATIFIED,
        }
    ),
    RunState.BLOCKED: frozenset(
        {
            RunState.BUILDING,
            RunState.SPECIFICATION_DEFECT,
        }
    ),
}

assert_catalog_covers(
    {
        str(source): tuple(str(destination) for destination in destinations)
        for source, destinations in ALLOWED_TRANSITIONS.items()
    }
)

_PHASE_STATE_KEYS: Mapping[RunState, str] = {
    RunState.PRODUCT_SPECIFICATION_RATIFIED: "product-specification",
    RunState.ARCHITECTURE_RATIFIED: "architecture",
    RunState.OPERATIONAL_MATURITY_RATIFIED: "operational-maturity",
}
_PHASE_ORDER = (
    "product-specification",
    "architecture",
    "operational-maturity",
)

# The anchor states past `preview`. Each names the artifact digest that transition MUST carry,
# in the same fail-closed shape `_PHASE_STATE_KEYS` uses for the three ratified states. Without
# these keys the two doctrinal anchor points required nothing but a non-empty actor string, so
# "the artifact shown to the human is byte-for-byte the artifact promoted" had nothing recording
# which artifact the human saw.
_ANCHOR_STATE_KEYS: Mapping[RunState, str] = {
    RunState.HUMAN_APPROVED: "candidate",
    RunState.PROMOTED: "promoted-artifact",
}


class RunStateError(ValueError):
    """A run could not be created, loaded, or transitioned without guessing."""


@dataclass(frozen=True)
class RunProjection:
    """Checked projection of the authoritative transition ledger."""

    run_id: str
    state: str
    target_digest: str
    source_digest: str
    target_state_digest: str
    target_state: Mapping[str, Any]
    generation: int
    phase_artifact_digests: Mapping[str, str]
    ledger_head: str
    created_at: int
    updated_at: int
    approved_candidate_digest: str = ""
    acceptance_obligation_catalog_digest: str = ""
    generation_artifact_digests: Mapping[str, str] = field(default_factory=dict)
    build_attempt_count: int = 0
    build_attempt_limit: int = 0
    schema_version: str = RUN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["target_state"] = dict(self.target_state)
        body["phase_artifact_digests"] = dict(self.phase_artifact_digests)
        body["generation_artifact_digests"] = dict(self.generation_artifact_digests)
        return body

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RunProjection:
        phase_raw = raw.get("phase_artifact_digests")
        generation_raw = raw.get("generation_artifact_digests")
        return cls(
            run_id=str(raw.get("run_id", "")),
            state=str(raw.get("state", "")),
            target_digest=str(raw.get("target_digest", "")),
            source_digest=str(raw.get("source_digest", "")),
            target_state_digest=str(raw.get("target_state_digest", "")),
            target_state=(
                dict(raw["target_state"]) if isinstance(raw.get("target_state"), Mapping) else {}
            ),
            generation=_as_int(raw.get("generation")),
            phase_artifact_digests=(
                {str(key): str(value) for key, value in phase_raw.items()}
                if isinstance(phase_raw, Mapping)
                else {}
            ),
            ledger_head=str(raw.get("ledger_head", "")),
            created_at=_as_int(raw.get("created_at")),
            updated_at=_as_int(raw.get("updated_at")),
            approved_candidate_digest=str(raw.get("approved_candidate_digest", "")),
            acceptance_obligation_catalog_digest=str(
                raw.get("acceptance_obligation_catalog_digest", "")
            ),
            generation_artifact_digests=(
                {str(key): str(value) for key, value in generation_raw.items()}
                if isinstance(generation_raw, Mapping)
                else {}
            ),
            build_attempt_count=_as_int(raw.get("build_attempt_count")),
            build_attempt_limit=_as_int(raw.get("build_attempt_limit")),
            schema_version=str(raw.get("schema_version", "")),
        )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _require_digest(value: str, field_name: str) -> None:
    if not _DIGEST.fullmatch(value):
        raise RunStateError(f"{field_name} must be a canonical sha256 digest")


def _require_generation_artifacts(
    digests: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, str]:
    """Require the complete immutable generation-readiness tuple on each build entry."""

    missing = [key for key in GENERATION_ARTIFACT_KEYS if not digests.get(key)]
    if missing:
        raise RunStateError(
            f"{context} requires generation artifact digest(s): {', '.join(missing)}"
        )
    generation = {key: str(digests[key]) for key in GENERATION_ARTIFACT_KEYS}
    for key, value in generation.items():
        _require_digest(value, f"generation_artifacts[{key!r}]")
    return generation


def _require_build_attempt(
    payload: Mapping[str, Any],
    *,
    expected_attempt: int,
    context: str,
) -> int:
    """Make convergence a ledger predicate rather than an agent promise."""

    attempt_number = payload.get("attempt_number")
    attempt_limit = payload.get("attempt_limit")
    if (
        isinstance(attempt_number, bool)
        or not isinstance(attempt_number, int)
        or attempt_number != expected_attempt
    ):
        raise RunStateError(
            f"{context} requires attempt_number {expected_attempt}, not {attempt_number!r}"
        )
    if isinstance(attempt_limit, bool) or not isinstance(attempt_limit, int) or attempt_limit < 1:
        raise RunStateError(f"{context} requires a positive attempt_limit")
    if attempt_number > attempt_limit:
        raise RunStateError(
            f"{context} exceeds the authorized build attempt limit "
            f"({attempt_number} > {attempt_limit})"
        )
    return attempt_limit


def _require_approval_identities(
    approver_identity: str,
    implementer_identity: str,
    *,
    context: str,
) -> None:
    """Human approval needs both identities present and distinct.

    ``LedgerEntry`` enforces distinctness only among the identities *actually present*, which is
    the right general default — a draft edit has no approver. ``human-approved`` is the state
    where that default is too weak: comparing an approver against an absent implementer proves
    nothing, so an approval with no recorded implementer would satisfy an SoD check vacuously.
    I2 requires implementer ≠ approver, so both are mandatory here and the transition fails
    closed without them.

    N.B. — an open doctrine question sits on top of this, raised by issue #4 (2026-08-05). The
    founder ratified three distinct enrolled principals for the SoD triad at ``enforcing`` while
    stating that n=1 is a legitimate bootstrap state and is where the project currently is.
    Those two cannot both hold with the implementer *recorded*: ``LedgerEntry.validate_sod``
    refuses any two present-and-equal identities unconditionally, in the core, under I2. So a
    lone human who implements and approves cannot reach ``human-approved`` at all — and before
    this control, n=1 reached it only by leaving ``implementer_identity`` empty, which is exactly
    the vacuous pass closed above. Recording the collapse honestly needs either an amendment to
    I2 or a represented-and-visible collapse; both are the founder's to decide, and neither is
    invented here. ``test_human_approval_by_the_implementer_is_refused`` pins the current
    behavior so the answer lands in one place.
    """
    approver = approver_identity.strip()
    implementer = implementer_identity.strip()
    if not approver:
        raise RunStateError(f"{context} requires an approver identity")
    if not implementer:
        raise RunStateError(
            f"{context} requires an implementer identity: distinctness from an absent "
            "implementer is unverifiable, not satisfied"
        )
    if approver == implementer:
        raise RunStateError(
            "approver and implementer must be distinct identities for human approval"
        )


#: The two receipts a `*-ratified` entry must name, keyed `{phase}:{role}-receipt` — the keys
#: `WorkflowEngine.ratify_phase` already records the verified envelope digests under.
_RATIFICATION_RECEIPT_ROLES = ("human", "validator")

#: The key suffixes that make a digest a ratification receipt, wherever it appears.
_RECEIPT_KEY_SUFFIXES = tuple(f":{role}-receipt" for role in _RATIFICATION_RECEIPT_ROLES)


def _receipt_keys(digests: Mapping[str, Any]) -> set[str]:
    """The receipt-shaped keys in one artifact-digest map, by suffix alone."""
    return {str(key) for key in digests if str(key).endswith(_RECEIPT_KEY_SUFFIXES)}


def _receipt_digests_in(digests: Mapping[str, Any]) -> set[str]:
    """The receipt digests one artifact-digest map spends.

    The single rule for "this digest has been used as a receipt". Both paths accumulate through
    it -- `transition` over the whole ledger, `_derive` entry by entry -- so neither can end up
    with a narrower notion of what is already spent than the other.
    """
    return {str(digests[key]) for key in _receipt_keys(digests)}


def _recorded_receipt_digests(records: Iterable[Mapping[str, Any]]) -> set[str]:
    """Every ratification-receipt digest already recorded in a run's ledger."""
    seen: set[str] = set()
    for record in records:
        digests = record.get("artifact_digests")
        if not isinstance(digests, Mapping):
            continue
        seen |= _receipt_digests_in(digests)
    return seen


def _require_receipts_belong_here(
    digests: Mapping[str, Any], artifact_keys: Collection[str], *, context: str
) -> None:
    """A receipt digest may only appear on the ratification of the phase it ratifies.

    N.B. This exists because the reuse rule counts a receipt digest as spent by key *suffix*
    anywhere in the ledger (``_recorded_receipt_digests``), while the ratification check only ever
    reaches the two keys belonging to the phase being ratified. Without this, a receipt-shaped key
    parked on some other entry -- another phase's ratification, or any non-ratifying transition --
    would spend a digest on the write path that ``_derive`` never sees as spent, so a
    directly-appended ledger could reuse it where ``transition`` refuses to. Two paths disagreeing
    about what is admissible is the whole defect class the derive-side checks exist to close, so
    close it by construction: there is exactly one place a receipt key is meaningful, and every
    other placement is refused rather than silently counted.

    ``WorkflowEngine.ratify_phase`` records exactly the two keys for the phase it ratified, so
    nothing on the real path is affected.
    """
    allowed = {
        f"{artifact_key}:{role}-receipt"
        for artifact_key in artifact_keys
        for role in _RATIFICATION_RECEIPT_ROLES
    }
    stray = sorted(_receipt_keys(digests) - allowed)
    if stray:
        raise RunStateError(
            f"{context} records receipt digest(s) {stray} that ratify nothing here: a receipt key "
            "belongs only to the ratification of its own phase"
        )


def _require_ratification_receipts(
    digests: Mapping[str, Any],
    phase_key: str,
    *,
    context: str,
    already_recorded: Collection[str] = (),
) -> set[str]:
    """A ratification names a human receipt and a distinct Validator receipt, or it is refused.

    The store does not — and must not — verify a signature: no key material lives in
    ``manifest.py`` and verification belongs behind the Tessera seam, where
    ``WorkflowEngine.ratify_phase`` does it against the exact artifact digest, run id, expected
    signer, and consumed nonces. What the store owns is *admissibility*: an entry that does not
    name both receipts is not a ratification, so the transition fails closed rather than recording
    one. Without this the requirement lived only in the workflow layer, and a control that lives
    only in the workflow layer is bypassable by anything holding a store.

    The three digests must be distinct. Two names for one envelope is one receipt — the same
    collapse ``ratify_phase`` refuses when the human and Validator ratifier identities match — and
    a receipt whose digest equals the artifact's cannot be an envelope containing a signature over
    that artifact. Either equality means the map was padded to satisfy the check.

    The phase artifact digest is required here too, not only by the caller. ``transition`` checks
    it before calling; ``_derive`` reads a map an appender controls, and an entry that omitted the
    top-level digest would otherwise reach the distinctness check with an empty artifact value —
    which is what makes "no receipt equals the artifact digest" meaningful in the first place.

    A receipt already recorded in this run cannot ratify again. A receipt is bound to one subject
    digest, so re-presenting one after a ``specification-defect`` would be a receipt over the bytes
    that defect just invalidated — "any new signed version invalidates old derived work," enforced
    where the re-ratification is written rather than trusted to the caller. Returns the two digests
    so a caller walking a ledger can accumulate them.
    """
    artifact = str(digests.get(phase_key, ""))
    if not artifact:
        raise RunStateError(f"{context} requires artifact digest {phase_key!r}")
    _require_digest(artifact, f"artifact_digests[{phase_key!r}]")
    values = [artifact]
    receipts: set[str] = set()
    for role in _RATIFICATION_RECEIPT_ROLES:
        key = f"{phase_key}:{role}-receipt"
        value = str(digests.get(key, ""))
        if not value:
            raise RunStateError(f"{context} requires receipt digest {key!r}")
        # Validated unstripped, on purpose: whitespace around a digest would let the check pass
        # on a value that is not the one recorded in the entry.
        _require_digest(value, f"artifact_digests[{key!r}]")
        if value in already_recorded:
            raise RunStateError(
                f"{context} reuses receipt digest {value} already recorded in this run: a receipt "
                "binds to one subject digest and cannot ratify a second version of it"
            )
        values.append(value)
        receipts.add(value)
    if len(set(values)) != len(values):
        raise RunStateError(
            f"{context} requires the artifact digest and both receipt digests to be distinct: "
            "one envelope cited twice is one receipt, not two"
        )
    return receipts


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _require_digest_keys(
    digests: Mapping[str, Any],
    keys: Iterable[str],
    *,
    context: str,
) -> None:
    for key in keys:
        value = str(digests.get(key, ""))
        if not value:
            raise RunStateError(f"{context} requires artifact digest {key!r}")
        _require_digest(value, f"artifact_digests[{key!r}]")


def _required_phase_keys(
    state: RunState,
    payload: Mapping[str, Any],
) -> frozenset[str]:
    if state in {
        RunState.TARGET_RESOLUTION_AUTHORIZED,
        RunState.TARGET_RESOLVED,
        RunState.INTAKE,
    }:
        return frozenset()
    if state is RunState.PRODUCT_SPECIFICATION_RATIFIED:
        return frozenset(_PHASE_ORDER[:1])
    if state is RunState.ARCHITECTURE_RATIFIED:
        return frozenset(_PHASE_ORDER[:2])
    if state is RunState.SPECIFICATION_DEFECT:
        defect_phase = str(payload.get("phase", ""))
        if defect_phase not in _PHASE_ORDER:
            raise RunStateError(
                "specification-defect transition requires payload.phase naming the affected phase"
            )
        return frozenset(_PHASE_ORDER[: _PHASE_ORDER.index(defect_phase)])
    return frozenset(_PHASE_ORDER)


class RunStore:
    """Filesystem-backed run store whose ledger, not projection, is authoritative."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.root = Path(root)
        self._clock = clock or (lambda: int(time.time()))

    def _run_dir(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise RunStateError(
                "run_id must start with an alphanumeric and contain only letters, numbers, "
                "dot, underscore, or dash"
            )
        path = self.root / run_id
        if path.is_symlink():
            raise RunStateError(f"run directory cannot be a symlink: {run_id}")
        return path

    def _ledger(self, run_id: str) -> Ledger:
        path = self._run_dir(run_id) / "ledger.jsonl"
        if path.is_symlink():
            raise RunStateError(f"run ledger cannot be a symlink: {run_id}")
        return Ledger(str(path))

    def _projection_path(self, run_id: str) -> Path:
        path = self._run_dir(run_id) / "run.json"
        if path.is_symlink():
            raise RunStateError(f"run projection cannot be a symlink: {run_id}")
        return path

    def create(
        self,
        run_id: str,
        *,
        target_digest: str,
        actor: str,
        artifact_digests: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        approver_identity: str = "",
        policy: SegregationPolicy | None = None,
    ) -> RunProjection:
        """Create a v4 run at the authorized target-resolution boundary.

        Ordinary intake is deliberately impossible here. The exact target state must first be
        recorded, then a second execution receipt must establish the verbatim source digest.
        """

        _require_digest(target_digest, "target_digest")
        if not actor.strip():
            raise RunStateError("actor is required")
        supplied = dict(artifact_digests or {})
        reserved_obligation_keys = {
            TRANSITION_OBLIGATION_SET_KEY,
            TRANSITION_OBLIGATION_REPORT_KEY,
        }
        if reserved_obligation_keys.intersection(supplied):
            raise RunStateError(
                "transition obligation digests are derived by the store, not supplied by callers"
            )
        for key, value in supplied.items():
            _require_digest(value, f"artifact_digests[{key!r}]")
        _require_digest_keys(
            supplied,
            (
                "target-manifest-source",
                "target-resolution-request",
                "target-resolution-receipt",
                "authority-genesis",
            ),
            context=str(RunState.TARGET_RESOLUTION_AUTHORIZED),
        )
        request_nonces = dict(payload or {}).get("authority_receipt_nonces")
        if not isinstance(request_nonces, list) or len(request_nonces) != 1:
            raise RunStateError(
                "target-resolution authorization requires exactly one authority receipt nonce"
            )
        run_dir = self._run_dir(run_id)
        if run_dir.exists() and not run_dir.is_dir():
            raise RunStateError(f"run path is not a directory: {run_id}")
        if (run_dir / "ledger.jsonl").exists() or (run_dir / "run.json").exists():
            raise RunStateError(f"run already exists: {run_id}")
        run_dir.mkdir(parents=True, exist_ok=True)
        created_at = self._clock()
        ledger = self._ledger(run_id)
        ledger.append(
            LedgerEntry(
                capability_id=run_id,
                from_state="",
                to_state=RunState.TARGET_RESOLUTION_AUTHORIZED,
                approver_identity=approver_identity,
                artifact_digests={
                    **supplied,
                    "target": target_digest,
                    "target-state": "",
                    "source": "",
                    "phase_artifacts": {},
                    "generation_artifacts": {},
                    ACCEPTANCE_OBLIGATION_CATALOG_STRUCTURAL_KEY: "",
                },
                payload={
                    **dict(payload or {}),
                    "generation": 1,
                    "run_schema_version": RUN_SCHEMA_VERSION,
                },
                actor=actor.strip(),
                created_at=str(created_at),
            ),
            policy,
        )
        projection = self._derive(run_id)
        self._write_projection(projection)
        return projection

    def record_target_state(
        self,
        run_id: str,
        *,
        target_state: Mapping[str, Any],
        actor: str,
        artifact_digests: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> RunProjection:
        """Record immutable target-state bytes after the authorized contact operation."""

        state_digest = digest_obj(dict(target_state))
        supplied = dict(artifact_digests or {})
        claimed = supplied.get("target-state", state_digest)
        if claimed != state_digest:
            raise RunStateError("target-state digest does not address the supplied document")
        return self.transition(
            run_id,
            RunState.TARGET_RESOLVED,
            actor=actor,
            artifact_digests={**supplied, "target-state": state_digest},
            payload={**dict(payload or {}), "target_state": dict(target_state)},
        )

    def authorize_intake(
        self,
        run_id: str,
        *,
        source_digest: str,
        actor: str,
        artifact_digests: Mapping[str, str],
        payload: Mapping[str, Any],
        approver_identity: str,
        policy: SegregationPolicy | None = None,
    ) -> RunProjection:
        """Establish Stage-E authority for one exact target-state and verbatim request."""

        _require_digest(source_digest, "source_digest")
        return self.transition(
            run_id,
            RunState.INTAKE,
            actor=actor,
            artifact_digests={**dict(artifact_digests), "source": source_digest},
            payload=payload,
            approver_identity=approver_identity,
            policy=policy,
        )

    def load(self, run_id: str) -> RunProjection:
        """Verify the ledger and require the convenience projection to match it exactly."""

        derived = self._derive(run_id)
        path = self._projection_path(run_id)
        if not path.is_file():
            raise RunStateError(
                f"run projection missing for {run_id}; run an explicit projection rebuild"
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunStateError(f"run projection is unreadable: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise RunStateError("run projection must be a JSON object")
        stored = RunProjection.from_dict(raw)
        if _canonical_json(stored.to_dict()) != _canonical_json(derived.to_dict()):
            raise RunStateError(
                "run projection does not match the authoritative ledger (stale or tampered)"
            )
        return derived

    def rebuild_projection(self, run_id: str) -> RunProjection:
        """Re-derive and atomically replace a projection from an intact ledger."""

        projection = self._derive(run_id)
        self._write_projection(projection)
        return projection

    def consumed_authority_nonces(self, run_id: str) -> frozenset[str]:
        """Return replay nonces only after the whole run ledger and projection verify."""

        self.load(run_id)
        nonces: set[str] = set()
        for record in self._ledger(run_id).entries():
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                raise RunStateError("run ledger entry has no payload object")
            raw_nonces = payload.get("authority_receipt_nonces", [])
            if not isinstance(raw_nonces, list):
                raise RunStateError("authority_receipt_nonces must be an array")
            nonces.update(str(nonce) for nonce in raw_nonces)
        return frozenset(nonces)

    def current_artifact_digests(self, run_id: str) -> Mapping[str, Any]:
        """Return the latest verified artifact map without trusting a stale snapshot.

        The second head check detects an append racing the projection read. Callers must retry
        rather than bind evidence to a mixture of two lifecycle states.
        """

        projection = self.load(run_id)
        entries = self._ledger(run_id).entries()
        if not entries or entries[-1].get("entry_hash") != projection.ledger_head:
            raise RunStateError("run ledger changed while artifact evidence was being read")
        raw = entries[-1].get("artifact_digests")
        if not isinstance(raw, Mapping):
            raise RunStateError("latest run ledger entry has no artifact digest map")
        return dict(raw)

    def execution_authority_digests(self, run_id: str) -> Mapping[str, str]:
        """Return the unique Stage-E artifact bindings from one verified run snapshot.

        Stage-E evidence is established at intake and need not be repeated on every later
        transition.  Harness consumers must nevertheless re-derive the retained request against
        that original authoritative entry, not trust a neighboring evidence file merely because
        it lives under the run directory.
        """

        projection = self.load(run_id)
        entries = self._ledger(run_id).verified_entries()
        if not entries or entries[-1].get("entry_hash") != projection.ledger_head:
            raise RunStateError("run ledger changed while Stage-E authority was being read")
        intake_entries = [record for record in entries if record.get("to_state") == RunState.INTAKE]
        if len(intake_entries) != 1:
            raise RunStateError("run ledger must contain exactly one Stage-E intake entry")
        raw = intake_entries[0].get("artifact_digests")
        if not isinstance(raw, Mapping):
            raise RunStateError("Stage-E intake has no artifact digest map")
        keys = (
            "execution-request",
            "execution-receipt",
            "authority-genesis",
            "target",
            "target-state",
            "source",
        )
        _require_digest_keys(raw, keys, context="Stage-E intake")
        bindings = {key: str(raw[key]) for key in keys}
        if bindings["target"] != projection.target_digest:
            raise RunStateError("Stage-E intake target differs from the current run subject")
        if bindings["target-state"] != projection.target_state_digest:
            raise RunStateError("Stage-E intake target-state differs from the current run subject")
        if bindings["source"] != projection.source_digest:
            raise RunStateError("Stage-E intake source differs from the current run subject")
        return bindings

    def transition(
        self,
        run_id: str,
        to_state: RunState | str,
        *,
        actor: str,
        artifact_digests: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        implementer_identity: str = "",
        verifier_identity: str = "",
        approver_identity: str = "",
        policy: SegregationPolicy | None = None,
    ) -> RunProjection:
        """Serialize one state transition with terminal resource sealing for this run."""

        try:
            resources = ResourceLedger(self._run_dir(run_id), run_id)
            with resources.run_transition_guard():
                return self._transition_guarded(
                    run_id,
                    to_state,
                    actor=actor,
                    artifact_digests=artifact_digests,
                    payload=payload,
                    implementer_identity=implementer_identity,
                    verifier_identity=verifier_identity,
                    approver_identity=approver_identity,
                    policy=policy,
                    resource_ledger=resources,
                )
        except ResourceLedgerError as exc:
            raise RunStateError(f"run transition guard failed: {exc}") from exc

    def _transition_guarded(
        self,
        run_id: str,
        to_state: RunState | str,
        *,
        actor: str,
        artifact_digests: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        implementer_identity: str = "",
        verifier_identity: str = "",
        approver_identity: str = "",
        policy: SegregationPolicy | None = None,
        resource_ledger: ResourceLedger,
    ) -> RunProjection:
        """Append one authorized state transition and refresh the checked projection."""

        current = self.load(run_id)
        if current.schema_version != RUN_SCHEMA_VERSION:
            raise RunStateError(
                "legacy run schema cannot advance under the generation-readiness controls; "
                "start a new run"
            )
        try:
            destination = RunState(to_state)
            source = RunState(current.state)
        except ValueError as exc:
            raise RunStateError(f"unsupported run state: {exc}") from exc
        if destination not in ALLOWED_TRANSITIONS[source]:
            raise RunStateError(f"transition refused: {source} -> {destination}")
        if not actor.strip():
            raise RunStateError("actor is required")

        # A terminal resource seal is the promotion commit point.  If the process crashed after
        # installing it but before appending PROMOTED, the CI state is intentionally resumable but
        # no longer an ordinary live run: only the exact promotion retry may follow.  Reopening
        # authoring would require a new run rather than silently breaking the sealed accounting.
        try:
            existing_resource_seal = resource_ledger.terminal_seal()
        except ResourceLedgerError as exc:
            raise RunStateError(f"run resource seal is invalid: {exc}") from exc
        if existing_resource_seal is not None and destination is not RunState.PROMOTED:
            raise RunStateError(
                "run resources are terminally sealed; only an idempotent promotion retry is "
                "allowed (start a new run to resume authoring)"
            )

        supplied = dict(artifact_digests or {})
        reserved_obligation_keys = {
            TRANSITION_OBLIGATION_SET_KEY,
            TRANSITION_OBLIGATION_REPORT_KEY,
        }
        if reserved_obligation_keys.intersection(supplied):
            raise RunStateError(
                "transition obligation digests are derived by the store, not supplied by callers"
            )
        for key, value in supplied.items():
            _require_digest(value, f"artifact_digests[{key!r}]")
        phases = dict(current.phase_artifact_digests)
        next_acceptance_catalog_digest = current.acceptance_obligation_catalog_digest
        transition_payload = dict(payload or {})
        next_target_state_digest = current.target_state_digest
        next_target_state = dict(current.target_state)
        next_source_digest = current.source_digest
        if destination is RunState.TARGET_RESOLVED:
            _require_digest_keys(
                supplied,
                ("target-state", "resource-ledger"),
                context=str(destination),
            )
            raw_target_state = transition_payload.get("target_state")
            if not isinstance(raw_target_state, Mapping):
                raise RunStateError("target-resolved requires payload.target_state")
            try:
                validate_document("target-state", raw_target_state)
            except DocumentValidationError as exc:
                raise RunStateError(str(exc)) from exc
            next_target_state_digest = digest_obj(dict(raw_target_state))
            next_target_state = dict(raw_target_state)
            if supplied["target-state"] != next_target_state_digest:
                raise RunStateError("target-state digest does not address payload.target_state")
            if raw_target_state.get("run_id") != run_id:
                raise RunStateError("target-state belongs to another run")
            if raw_target_state.get("target_manifest_digest") != current.target_digest:
                raise RunStateError("target-state binds another target manifest")
            if raw_target_state.get("generation") != current.generation:
                raise RunStateError("target-state binds another run generation")
            if raw_target_state.get("resource_ledger_head") != supplied["resource-ledger"]:
                raise RunStateError("target-state resource ledger head does not match transition")
        elif destination is RunState.INTAKE:
            _require_digest_keys(
                supplied,
                ("execution-request", "execution-receipt", "authority-genesis", "source"),
                context=str(destination),
            )
            verified_entries = self._ledger(run_id).verified_entries()
            if (
                not verified_entries
                or verified_entries[-1].get("entry_hash") != current.ledger_head
            ):
                raise RunStateError("run ledger changed while Stage-E authority was being bound")
            genesis_digests = verified_entries[0].get("artifact_digests")
            if not isinstance(genesis_digests, Mapping):
                raise RunStateError("run genesis has no artifact digest map")
            if supplied["authority-genesis"] != genesis_digests.get("authority-genesis"):
                raise RunStateError("Stage-E authority genesis differs from Stage R")
            request_nonces = transition_payload.get("authority_receipt_nonces")
            if not isinstance(request_nonces, list) or len(request_nonces) != 1:
                raise RunStateError(
                    "execution authorization requires exactly one authority receipt nonce"
                )
            if not current.target_state_digest or not current.target_state:
                raise RunStateError("intake requires a previously recorded target-state")
            next_source_digest = supplied["source"]
        else:
            forbidden_subject_keys = sorted(
                {"target", "target-state", "source"}.intersection(supplied)
            )
            if forbidden_subject_keys:
                raise RunStateError(
                    f"{destination} may not resupply run subject digest(s): "
                    + ", ".join(forbidden_subject_keys)
                )
        phase_key = _PHASE_STATE_KEYS.get(destination)
        catalog_activation = destination is RunState.BUILDING and not next_acceptance_catalog_digest
        changed_tests_raw = transition_payload.get("changed_existing_tests", [])
        if not isinstance(changed_tests_raw, list):
            raise RunStateError("changed_existing_tests must be an exact array")
        changed_tests = [str(test_id) for test_id in changed_tests_raw]
        test_change_activation = bool(changed_tests)
        if test_change_activation and destination is not RunState.BUILDING:
            raise RunStateError(
                "test expectation changes may be authorized only when entering building"
            )
        ratified_artifact_keys = {
            key
            for key in (
                phase_key,
                ACCEPTANCE_OBLIGATION_CATALOG_KEY if catalog_activation else None,
                TEST_CHANGE_AUTHORIZATION_KEY if test_change_activation else None,
            )
            if key is not None
        }
        _require_receipts_belong_here(
            supplied,
            ratified_artifact_keys,
            context=str(destination),
        )
        already_recorded_receipts = set(_recorded_receipt_digests(self._ledger(run_id).entries()))
        if phase_key:
            phase_digest = supplied.get(phase_key, "")
            if not phase_digest:
                raise RunStateError(f"{destination} requires artifact digest {phase_key!r}")
            already_recorded_receipts |= _require_ratification_receipts(
                supplied,
                phase_key,
                context=str(destination),
                already_recorded=already_recorded_receipts,
            )
            phases[phase_key] = phase_digest
        if catalog_activation:
            catalog_digest = str(supplied.get(ACCEPTANCE_OBLIGATION_CATALOG_KEY, ""))
            if not catalog_digest:
                raise RunStateError(
                    "first build requires a human and Validator ratified "
                    "acceptance-obligation catalog"
                )
            already_recorded_receipts |= _require_ratification_receipts(
                supplied,
                ACCEPTANCE_OBLIGATION_CATALOG_KEY,
                context=str(destination),
                already_recorded=already_recorded_receipts,
            )
            next_acceptance_catalog_digest = catalog_digest
        elif ACCEPTANCE_OBLIGATION_CATALOG_KEY in supplied:
            raise RunStateError(
                "acceptance-obligation catalog may be supplied only on its first build activation"
            )
        if test_change_activation:
            test_change_digest = str(supplied.get(TEST_CHANGE_AUTHORIZATION_KEY, ""))
            if not test_change_digest:
                raise RunStateError(
                    "changed existing tests require a human and Validator ratified "
                    "test-change-authorization"
                )
            already_recorded_receipts |= _require_ratification_receipts(
                supplied,
                TEST_CHANGE_AUTHORIZATION_KEY,
                context=str(destination),
                already_recorded=already_recorded_receipts,
            )
        elif TEST_CHANGE_AUTHORIZATION_KEY in supplied:
            raise RunStateError(
                "test-change authorization may be supplied only with an exact nonempty "
                "changed_existing_tests set"
            )

        expected_authority_nonces = (
            (1 if destination is RunState.INTAKE else 0)
            + (2 if catalog_activation else 0)
            + (2 if test_change_activation else 0)
        )
        transition_nonces = transition_payload.get("authority_receipt_nonces", [])
        if not isinstance(transition_nonces, list):
            raise RunStateError("authority_receipt_nonces must be an array")
        normalized_nonces = [str(nonce) for nonce in transition_nonces]
        allowed_authority_nonce_counts = {expected_authority_nonces}
        if phase_key:
            # Legacy direct-store tests and ledgers predate nonce recording for phase receipts;
            # the workflow path records both. Receipt digests remain mandatory in either form.
            allowed_authority_nonce_counts.add(expected_authority_nonces + 2)
        if len(normalized_nonces) not in allowed_authority_nonce_counts:
            raise RunStateError(
                f"{destination} requires authority receipt nonce count in "
                f"{sorted(allowed_authority_nonce_counts)} for the ratifications recorded on "
                "this transition"
            )
        if any(not nonce.strip() for nonce in normalized_nonces):
            raise RunStateError("authority receipt nonces must be nonempty")
        if len(normalized_nonces) != len(set(normalized_nonces)):
            raise RunStateError("authority receipt nonces must be unique")
        replayed_nonces = sorted(set(normalized_nonces) & self.consumed_authority_nonces(run_id))
        if replayed_nonces:
            raise RunStateError("authority receipt nonce replay: " + ", ".join(replayed_nonces))
        if destination is RunState.SPECIFICATION_DEFECT:
            next_acceptance_catalog_digest = ""

        if destination is RunState.PREVIEW:
            _require_digest_keys(
                supplied,
                (
                    "candidate",
                    "acceptance-tests",
                    ACCEPTANCE_OBLIGATION_REPORT_KEY,
                    "evidence-bundle",
                    "evidence-envelope",
                ),
                context=str(destination),
            )
            prior_artifacts = self.current_artifact_digests(run_id)
            trusted_evidence = {
                key: str(prior_artifacts.get(key, ""))
                for key in (
                    "candidate",
                    "acceptance-tests",
                    "coder-output-snapshot",
                    "tester-output-snapshot",
                )
            }
            _require_digest_keys(
                trusted_evidence,
                trusted_evidence,
                context=f"{destination} prior validation",
            )
            for key in ("candidate", "acceptance-tests"):
                if supplied[key] != trusted_evidence[key]:
                    raise RunStateError(
                        f"{destination} changes {key} after immutable validation began"
                    )
            try:
                from factory_runtime.acceptance_obligations import (
                    AcceptanceObligationError,
                    verify_retained_acceptance_obligation_report,
                )

                verify_retained_acceptance_obligation_report(
                    self._run_dir(run_id),
                    catalog_digest=next_acceptance_catalog_digest,
                    report_digest=supplied[ACCEPTANCE_OBLIGATION_REPORT_KEY],
                    run_id=run_id,
                    generation=current.generation,
                    source=str(source),
                    destination=str(destination),
                    target_state_digest=current.target_state_digest,
                    resolved_commit=str(current.target_state.get("resolved_commit", "")),
                    resolved_tree=str(current.target_state.get("resolved_tree", "")),
                    phase_artifact_digests=phases,
                    candidate_digest=supplied["candidate"],
                    acceptance_tests_digest=supplied["acceptance-tests"],
                    trusted_evidence_digests=trusted_evidence,
                )
            except AcceptanceObligationError as exc:
                raise RunStateError(
                    f"{destination} acceptance-obligation report is invalid: {exc}"
                ) from exc

        generation = dict(current.generation_artifact_digests)
        if destination is RunState.BUILDING:
            generation = _require_generation_artifacts(
                supplied,
                context=str(destination),
            )
            attempt_limit = _require_build_attempt(
                transition_payload,
                expected_attempt=current.build_attempt_count + 1,
                context=str(destination),
            )
            if current.build_attempt_limit and attempt_limit > current.build_attempt_limit:
                raise RunStateError(
                    "building cannot raise the attempt limit after generation starts"
                )
        elif destination is RunState.SPECIFICATION_DEFECT or phase_key:
            generation = {}

        # Anchor states carry authority, not just an actor. Each check is fail-closed: an
        # omission refuses the transition rather than recording an unauthorized anchor.
        anchor_key = _ANCHOR_STATE_KEYS.get(destination)
        if anchor_key and not supplied.get(anchor_key, ""):
            raise RunStateError(f"{destination} requires artifact digest {anchor_key!r}")
        if destination is RunState.HUMAN_APPROVED:
            _require_approval_identities(
                approver_identity, implementer_identity, context=str(destination)
            )
        if destination is RunState.PROMOTED:
            approved = current.approved_candidate_digest
            if not approved:
                raise RunStateError(
                    f"{destination} requires a previously approved candidate digest"
                )
            if supplied.get(anchor_key or "", "") != approved:
                raise RunStateError(
                    "promoted artifact does not match the approved candidate digest "
                    "(the artifact promoted must be byte-for-byte what was approved)"
                )

        required_phase_keys = _required_phase_keys(destination, transition_payload)
        phases = {key: phases[key] for key in _PHASE_ORDER if key in required_phase_keys}
        if set(phases) != set(required_phase_keys):
            missing = sorted(required_phase_keys - phases.keys())
            raise RunStateError(
                f"{destination} requires prior ratified phase artifacts: {', '.join(missing)}"
            )

        if destination is RunState.PROMOTED:
            # Promotion is terminal only when resource accounting is terminal too.  This lives
            # in RunStore rather than the harness so a direct state transition cannot route
            # around cleanup.  Sealing serializes against supported resource appends, is
            # resumable after a crash, and gives the run ledger an exact head to bind.
            try:
                seal = resource_ledger.seal_for_close(
                    actor=actor,
                    transition_guarded=True,
                )
            except ResourceLedgerError as exc:
                raise RunStateError(
                    "promoted requires all run resources to have admissible terminal "
                    f"dispositions and a durable seal: {exc}"
                ) from exc
            promotion_resource_digests = {
                "resource-ledger": str(seal["ledger_head"]),
                "resource-ledger-seal": str(seal["seal_digest"]),
            }
            for key, value in promotion_resource_digests.items():
                claimed = supplied.get(key)
                if claimed is not None and claimed != value:
                    raise RunStateError(
                        f"promoted {key} digest does not match the verified resource seal"
                    )
                supplied[key] = value

        now = self._clock()
        try:
            obligation_set, obligation_report = derive_transition_obligations(
                run_id=run_id,
                generation=current.generation,
                source=str(source),
                destination=str(destination),
                prior_ledger_head=current.ledger_head,
                target_state_digest=next_target_state_digest,
                target_state=next_target_state,
                phase_artifact_digests=phases,
                acceptance_obligation_catalog_digest=next_acceptance_catalog_digest,
                supplied_artifact_digests=supplied,
                payload=transition_payload,
                approved_candidate_digest=current.approved_candidate_digest,
                recorded_at=now,
                implementer_identity=implementer_identity,
                verifier_identity=verifier_identity,
                approver_identity=approver_identity,
            )
            obligation_set_digest, obligation_report_digest = retain_transition_obligations(
                self._run_dir(run_id),
                obligation_set,
                obligation_report,
            )
        except TransitionObligationError as exc:
            raise RunStateError(f"state-triggered obligation gate refused: {exc}") from exc
        supplied[TRANSITION_OBLIGATION_SET_KEY] = obligation_set_digest
        supplied[TRANSITION_OBLIGATION_REPORT_KEY] = obligation_report_digest
        try:
            self._ledger(run_id).append(
                LedgerEntry(
                    capability_id=run_id,
                    from_state=source,
                    to_state=destination,
                    implementer_identity=implementer_identity,
                    verifier_identity=verifier_identity,
                    approver_identity=approver_identity,
                    artifact_digests={
                        **supplied,
                        "target": current.target_digest,
                        "target-state": next_target_state_digest,
                        "source": next_source_digest,
                        "phase_artifacts": phases,
                        "generation_artifacts": generation,
                        ACCEPTANCE_OBLIGATION_CATALOG_STRUCTURAL_KEY: (
                            next_acceptance_catalog_digest
                        ),
                    },
                    payload=transition_payload,
                    actor=actor.strip(),
                    created_at=str(now),
                ),
                policy,
                expected_head=current.ledger_head,
            )
        except LedgerIntegrityError as exc:
            raise RunStateError(
                "run changed after the transition subject was derived; retry from the new head"
            ) from exc
        projection = self._derive(run_id)
        self._write_projection(projection)
        return projection

    def _derive(self, run_id: str) -> RunProjection:
        ledger = self._ledger(run_id)
        try:
            entries = ledger.verified_entries()
        except LedgerIntegrityError as exc:
            raise RunStateError(f"run ledger verification failed: {exc}") from exc
        if not entries:
            raise RunStateError(f"run does not exist or has no ledger entries: {run_id}")

        prior = ""
        target_digest = ""
        source_digest = ""
        target_state_digest = ""
        target_state: dict[str, Any] = {}
        authority_genesis_digest = ""
        generation = 0
        approved_candidate = ""
        phase_artifacts: dict[str, str] = {}
        generation_artifacts: dict[str, str] = {}
        acceptance_obligation_catalog_digest = ""
        validation_evidence: dict[str, str] = {}
        build_attempt_count = 0
        build_attempt_limit = 0
        schema_version = ""
        created_at = 0
        updated_at = 0
        current = ""
        consumed_nonces: set[str] = set()
        recorded_receipts: set[str] = set()
        for index, record in enumerate(entries):
            if record.get("capability_id") != run_id:
                raise RunStateError(f"ledger entry {index} belongs to another run")
            destination_raw = str(record.get("to_state", ""))
            try:
                destination = RunState(destination_raw)
            except ValueError as exc:
                raise RunStateError(
                    f"ledger entry {index} has unsupported state {destination_raw!r}"
                ) from exc
            source_raw = str(record.get("from_state", ""))
            if index > 0:
                if source_raw != prior:
                    raise RunStateError(
                        f"ledger entry {index} from_state does not match prior state"
                    )
                try:
                    source = RunState(source_raw)
                except ValueError as exc:
                    raise RunStateError(
                        f"ledger entry {index} has unsupported source state {source_raw!r}"
                    ) from exc
                if destination not in ALLOWED_TRANSITIONS[source]:
                    raise RunStateError(
                        f"ledger entry {index} records forbidden transition "
                        f"{source} -> {destination}"
                    )

            digests = record.get("artifact_digests")
            if not isinstance(digests, Mapping):
                raise RunStateError(f"ledger entry {index} has no artifact digest map")
            payload_raw = record.get("payload")
            if not isinstance(payload_raw, Mapping):
                raise RunStateError(f"ledger entry {index} has no payload object")
            stamp = _as_int(record.get("created_at"))
            if stamp <= 0:
                raise RunStateError(f"ledger entry {index} has no valid created_at")
            prior_approved_candidate = approved_candidate
            if index == 0:
                target_digest = str(digests.get("target", ""))
                source_digest = str(digests.get("source", ""))
                _require_digest(target_digest, "target_digest")
                schema_version = str(payload_raw.get("run_schema_version", ""))
                if schema_version not in {*LEGACY_RUN_SCHEMA_VERSIONS, RUN_SCHEMA_VERSION}:
                    raise RunStateError(
                        f"run genesis has unsupported schema version {schema_version!r}"
                    )
                expected_genesis = (
                    RunState.TARGET_RESOLUTION_AUTHORIZED
                    if schema_version in TARGET_STATE_RUN_SCHEMA_VERSIONS
                    else RunState.INTAKE
                )
                if source_raw or destination is not expected_genesis:
                    raise RunStateError(
                        "run genesis must transition from empty to "
                        f"{expected_genesis} for {schema_version}"
                    )
                if schema_version in TARGET_STATE_RUN_SCHEMA_VERSIONS:
                    if source_digest or str(digests.get("target-state", "")):
                        raise RunStateError(
                            "v3 target-resolution genesis cannot preselect source or target-state"
                        )
                    generation = _as_int(payload_raw.get("generation"))
                    if generation != 1:
                        raise RunStateError("v3 run genesis requires generation 1")
                    _require_digest_keys(
                        digests,
                        (
                            "target-manifest-source",
                            "target-resolution-request",
                            "target-resolution-receipt",
                            "authority-genesis",
                        ),
                        context="v3 target-resolution genesis",
                    )
                    authority_genesis_digest = str(digests["authority-genesis"])
                else:
                    _require_digest(source_digest, "source_digest")
            elif digests.get("target") != target_digest:
                raise RunStateError(f"ledger entry {index} changes the target manifest")

            if schema_version in TARGET_STATE_RUN_SCHEMA_VERSIONS:
                declared_target_state = str(digests.get("target-state", ""))
                declared_source = str(digests.get("source", ""))
                if destination is RunState.TARGET_RESOLVED:
                    _require_digest_keys(
                        digests,
                        ("target-state", "resource-ledger"),
                        context=f"ledger entry {index} target resolution",
                    )
                    raw_target_state = payload_raw.get("target_state")
                    if not isinstance(raw_target_state, Mapping):
                        raise RunStateError(
                            f"ledger entry {index} target resolution has no target_state"
                        )
                    try:
                        validate_document("target-state", raw_target_state)
                    except DocumentValidationError as exc:
                        raise RunStateError(f"ledger entry {index}: {exc}") from exc
                    candidate_target_state = dict(raw_target_state)
                    if digest_obj(candidate_target_state) != declared_target_state:
                        raise RunStateError(
                            f"ledger entry {index} target-state digest does not re-derive"
                        )
                    if candidate_target_state.get("run_id") != run_id:
                        raise RunStateError(f"ledger entry {index} target-state belongs elsewhere")
                    if candidate_target_state.get("target_manifest_digest") != target_digest:
                        raise RunStateError(
                            f"ledger entry {index} target-state binds another manifest"
                        )
                    if candidate_target_state.get("generation") != generation:
                        raise RunStateError(
                            f"ledger entry {index} target-state binds another generation"
                        )
                    if candidate_target_state.get("resource_ledger_head") != digests.get(
                        "resource-ledger"
                    ):
                        raise RunStateError(
                            f"ledger entry {index} target-state resource head mismatch"
                        )
                    if declared_source:
                        raise RunStateError(
                            f"ledger entry {index} establishes source before Stage E"
                        )
                    target_state_digest = declared_target_state
                    target_state = candidate_target_state
                elif destination is RunState.INTAKE:
                    if not target_state_digest or declared_target_state != target_state_digest:
                        raise RunStateError(
                            f"ledger entry {index} intake changes or omits target-state"
                        )
                    _require_digest_keys(
                        digests,
                        (
                            "execution-request",
                            "execution-receipt",
                            "authority-genesis",
                            "source",
                        ),
                        context=f"ledger entry {index} intake",
                    )
                    if digests.get("authority-genesis") != authority_genesis_digest:
                        raise RunStateError(
                            f"ledger entry {index} Stage-E authority genesis differs from Stage R"
                        )
                    source_digest = declared_source
                elif index == 0:
                    if declared_target_state or declared_source:
                        raise RunStateError(
                            "v3 target-resolution genesis preselects execution subject"
                        )
                else:
                    if declared_target_state != target_state_digest:
                        raise RunStateError(f"ledger entry {index} changes or omits target-state")
                    if not source_digest or declared_source != source_digest:
                        raise RunStateError(f"ledger entry {index} changes or omits source")
            elif index > 0 and digests.get("source") != source_digest:
                raise RunStateError(f"ledger entry {index} changes the legacy run subject")

            derived_phase_key = _PHASE_STATE_KEYS.get(destination)
            derived_catalog_activation = (
                schema_version == RUN_SCHEMA_VERSION
                and destination is RunState.BUILDING
                and not acceptance_obligation_catalog_digest
            )
            derived_changed_tests_raw = payload_raw.get("changed_existing_tests", [])
            if not isinstance(derived_changed_tests_raw, list):
                raise RunStateError(
                    f"ledger entry {index} changed_existing_tests must be an exact array"
                )
            derived_changed_tests = [str(test_id) for test_id in derived_changed_tests_raw]
            derived_test_change_activation = bool(derived_changed_tests)
            if derived_test_change_activation and destination is not RunState.BUILDING:
                raise RunStateError(
                    f"ledger entry {index} authorizes a test expectation change outside building"
                )
            derived_ratified_keys = {
                key
                for key in (
                    derived_phase_key,
                    (ACCEPTANCE_OBLIGATION_CATALOG_KEY if derived_catalog_activation else None),
                    (TEST_CHANGE_AUTHORIZATION_KEY if derived_test_change_activation else None),
                )
                if key is not None
            }
            _require_receipts_belong_here(
                digests, derived_ratified_keys, context=f"ledger entry {index}"
            )
            entry_recorded_receipts = set(recorded_receipts)
            if derived_phase_key:
                # Same reason as the anchor states below: the chain proves an entry was not
                # edited, not that it was written through `transition`. A direct append chains
                # validly and would otherwise project as a ratification with no receipts.
                entry_recorded_receipts |= _require_ratification_receipts(
                    digests,
                    derived_phase_key,
                    context=f"ledger entry {index} ratification",
                    already_recorded=entry_recorded_receipts,
                )
                # The entry records the ratified artifact twice — at the top level and in the
                # phase map. `transition` writes one value into both; two records of the same
                # digest that disagree is inadmissible rather than a matter of which one wins.
                phases_declared = digests.get("phase_artifacts")
                if isinstance(phases_declared, Mapping) and str(
                    phases_declared.get(derived_phase_key, "")
                ) != str(digests.get(derived_phase_key, "")):
                    raise RunStateError(
                        f"ledger entry {index} disagrees with itself about the "
                        f"{derived_phase_key!r} artifact digest"
                    )
            if derived_catalog_activation:
                entry_recorded_receipts |= _require_ratification_receipts(
                    digests,
                    ACCEPTANCE_OBLIGATION_CATALOG_KEY,
                    context=f"ledger entry {index} acceptance-obligation catalog",
                    already_recorded=entry_recorded_receipts,
                )
                acceptance_obligation_catalog_digest = str(
                    digests[ACCEPTANCE_OBLIGATION_CATALOG_KEY]
                )
            elif ACCEPTANCE_OBLIGATION_CATALOG_KEY in digests:
                raise RunStateError(
                    f"ledger entry {index} supplies an acceptance-obligation catalog outside "
                    "its first build activation"
                )
            if derived_test_change_activation:
                entry_recorded_receipts |= _require_ratification_receipts(
                    digests,
                    TEST_CHANGE_AUTHORIZATION_KEY,
                    context=f"ledger entry {index} test-change authorization",
                    already_recorded=entry_recorded_receipts,
                )
            elif TEST_CHANGE_AUTHORIZATION_KEY in digests:
                raise RunStateError(
                    f"ledger entry {index} supplies a test-change authorization without an "
                    "exact nonempty changed_existing_tests set"
                )
            # Accumulated by the same rule `transition` reads the ledger with, and for every entry
            # rather than only the ratifying ones, so the two paths cannot disagree about which
            # digests are spent.
            recorded_receipts |= _receipt_digests_in(digests)

            if destination is RunState.SPECIFICATION_DEFECT:
                acceptance_obligation_catalog_digest = ""
            if schema_version == RUN_SCHEMA_VERSION:
                declared_acceptance_catalog = str(
                    digests.get(ACCEPTANCE_OBLIGATION_CATALOG_STRUCTURAL_KEY, "")
                )
                if declared_acceptance_catalog != acceptance_obligation_catalog_digest:
                    raise RunStateError(
                        f"ledger entry {index} changes or omits the active "
                        "acceptance-obligation catalog"
                    )

            if schema_version in GENERATION_RUN_SCHEMA_VERSIONS:
                if destination is RunState.BUILDING:
                    generation_artifacts = _require_generation_artifacts(
                        digests,
                        context=f"ledger entry {index} building",
                    )
                    candidate_limit = _require_build_attempt(
                        payload_raw,
                        expected_attempt=build_attempt_count + 1,
                        context=f"ledger entry {index} building",
                    )
                    if build_attempt_limit and candidate_limit > build_attempt_limit:
                        raise RunStateError(f"ledger entry {index} raises the build attempt limit")
                    build_attempt_limit = (
                        min(build_attempt_limit, candidate_limit)
                        if build_attempt_limit
                        else candidate_limit
                    )
                    build_attempt_count += 1
                elif destination is RunState.SPECIFICATION_DEFECT or derived_phase_key:
                    generation_artifacts = {}
                    if destination is RunState.SPECIFICATION_DEFECT:
                        build_attempt_count = 0
                        build_attempt_limit = 0
                declared_generation = digests.get("generation_artifacts")
                if not isinstance(declared_generation, Mapping):
                    raise RunStateError(f"ledger entry {index} has no generation artifact map")
                candidate_generation = {
                    str(key): str(value) for key, value in declared_generation.items()
                }
                if candidate_generation != generation_artifacts:
                    raise RunStateError(
                        f"ledger entry {index} changes or omits generation artifacts"
                    )
                for key, value in candidate_generation.items():
                    _require_digest(value, f"generation_artifacts[{key!r}]")

            # Read from the same table `transition` reads, not by naming the two states again.
            # N.B. The digest requirement is generic on the write path (`_ANCHOR_STATE_KEYS`) and
            # was destination-by-destination here. Nothing was unenforced -- the table has exactly
            # the two entries both paths spell out -- but adding a third would have been picked up
            # by `transition` and silently skipped by `_derive`, and `_derive` being the weaker of
            # the two is the defect class these checks exist to close.
            derived_anchor_key = _ANCHOR_STATE_KEYS.get(destination)
            if derived_anchor_key:
                _require_digest(
                    str(digests.get(derived_anchor_key, "")),
                    f"ledger entry {index} {derived_anchor_key} digest",
                )

            if destination is RunState.HUMAN_APPROVED:
                approved_candidate = str(digests.get("candidate", ""))
                # `_derive` is the authority, so it must refuse what `transition` refuses. The
                # hash chain catches an edited entry; it does not catch one appended through the
                # ledger directly, which chains validly and would otherwise project as a
                # legitimate approval.
                _require_approval_identities(
                    str(record.get("approver_identity", "")),
                    str(record.get("implementer_identity", "")),
                    context=f"ledger entry {index} human approval",
                )
            elif destination is RunState.SPECIFICATION_DEFECT:
                # Approval binds the prior candidate under the prior phase versions. A defect
                # invalidates both, so retaining the digest in the projection would present
                # stale human authority to downstream readers even though promotion is no
                # longer reachable without another approval.
                approved_candidate = ""
            elif destination is RunState.PROMOTED:
                promoted = str(digests.get("promoted-artifact", ""))
                if promoted != approved_candidate:
                    raise RunStateError(
                        f"ledger entry {index} promotes a digest that was never approved"
                    )
                if schema_version in TARGET_STATE_RUN_SCHEMA_VERSIONS:
                    _require_digest(
                        str(digests.get("resource-ledger", "")),
                        f"ledger entry {index} promotion resource-ledger digest",
                    )
                    _require_digest(
                        str(digests.get("resource-ledger-seal", "")),
                        f"ledger entry {index} promotion resource-ledger-seal digest",
                    )
                    try:
                        seal, _ = ResourceLedger(
                            self._run_dir(run_id), run_id
                        ).verify_sealed_for_close()
                    except ResourceLedgerError as exc:
                        raise RunStateError(
                            f"ledger entry {index} has no valid terminal resource seal: {exc}"
                        ) from exc
                    if digests.get("resource-ledger") != seal["ledger_head"]:
                        raise RunStateError(
                            f"ledger entry {index} resource-ledger head differs from terminal seal"
                        )
                    if digests.get("resource-ledger-seal") != seal["seal_digest"]:
                        raise RunStateError(
                            f"ledger entry {index} resource-ledger seal digest does not match"
                        )

            phases_raw = digests.get("phase_artifacts")
            if not isinstance(phases_raw, Mapping):
                raise RunStateError(f"ledger entry {index} has no phase artifact map")
            candidate_phases = {str(key): str(value) for key, value in phases_raw.items()}
            raw_nonces = payload_raw.get("authority_receipt_nonces", [])
            if not isinstance(raw_nonces, list):
                raise RunStateError(
                    f"ledger entry {index} authority_receipt_nonces must be an array"
                )
            entry_nonces = [str(nonce) for nonce in raw_nonces]
            if any(not nonce.strip() for nonce in entry_nonces):
                raise RunStateError(f"ledger entry {index} contains an empty authority nonce")
            if len(entry_nonces) != len(set(entry_nonces)):
                raise RunStateError(f"ledger entry {index} repeats an authority nonce")
            if schema_version == RUN_SCHEMA_VERSION:
                expected_entry_nonces = (
                    (
                        1
                        if destination
                        in {
                            RunState.TARGET_RESOLUTION_AUTHORIZED,
                            RunState.INTAKE,
                        }
                        else 0
                    )
                    + (2 if derived_catalog_activation else 0)
                    + (2 if derived_test_change_activation else 0)
                )
                allowed_entry_nonce_counts = {expected_entry_nonces}
                if derived_phase_key:
                    allowed_entry_nonce_counts.add(expected_entry_nonces + 2)
                if len(entry_nonces) not in allowed_entry_nonce_counts:
                    raise RunStateError(
                        f"ledger entry {index} {destination} requires authority nonce count in "
                        f"{sorted(allowed_entry_nonce_counts)} for the authority artifacts "
                        "recorded on that entry"
                    )
            elif schema_version in TARGET_STATE_RUN_SCHEMA_VERSIONS:
                if (
                    destination
                    in {
                        RunState.TARGET_RESOLUTION_AUTHORIZED,
                        RunState.INTAKE,
                    }
                    and len(entry_nonces) != 1
                ):
                    raise RunStateError(
                        f"ledger entry {index} {destination} requires exactly one authority nonce"
                    )
                if derived_catalog_activation and len(entry_nonces) != 2:
                    raise RunStateError(
                        f"ledger entry {index} acceptance-obligation catalog activation "
                        "requires exactly two authority nonces"
                    )
                if destination is RunState.TARGET_RESOLVED and entry_nonces:
                    raise RunStateError(
                        f"ledger entry {index} target resolution may not consume authority"
                    )
            replayed = sorted(set(entry_nonces) & consumed_nonces)
            if replayed:
                raise RunStateError(
                    f"ledger entry {index} replays authority nonce(s): {', '.join(replayed)}"
                )
            consumed_nonces.update(entry_nonces)
            required_phase_keys = _required_phase_keys(destination, payload_raw)
            if set(candidate_phases) != set(required_phase_keys):
                raise RunStateError(
                    f"ledger entry {index} has phase artifacts inconsistent with {destination}"
                )
            for key, value in candidate_phases.items():
                _require_digest(value, f"phase_artifacts[{key!r}]")
            phase_artifacts = candidate_phases

            if schema_version == RUN_SCHEMA_VERSION:
                validation_keys = (
                    "candidate",
                    "acceptance-tests",
                    "coder-output-snapshot",
                    "tester-output-snapshot",
                )
                if destination is RunState.BUILDING:
                    validation_evidence = {}
                elif destination is RunState.VALIDATING:
                    _require_digest_keys(
                        digests,
                        validation_keys,
                        context=f"ledger entry {index} validating",
                    )
                    validation_evidence = {key: str(digests[key]) for key in validation_keys}
                elif destination is RunState.PREVIEW:
                    _require_digest_keys(
                        digests,
                        (
                            "candidate",
                            "acceptance-tests",
                            ACCEPTANCE_OBLIGATION_REPORT_KEY,
                            "evidence-bundle",
                            "evidence-envelope",
                        ),
                        context=f"ledger entry {index} preview",
                    )
                    if not validation_evidence:
                        raise RunStateError(
                            f"ledger entry {index} preview has no immutable validation subject"
                        )
                    for key in ("candidate", "acceptance-tests"):
                        if digests[key] != validation_evidence[key]:
                            raise RunStateError(
                                f"ledger entry {index} preview changes {key} after validation"
                            )
                    try:
                        from factory_runtime.acceptance_obligations import (
                            AcceptanceObligationError,
                            verify_retained_acceptance_obligation_report,
                        )

                        verify_retained_acceptance_obligation_report(
                            self._run_dir(run_id),
                            catalog_digest=acceptance_obligation_catalog_digest,
                            report_digest=str(digests[ACCEPTANCE_OBLIGATION_REPORT_KEY]),
                            run_id=run_id,
                            generation=generation,
                            source=source_raw,
                            destination=str(destination),
                            target_state_digest=target_state_digest,
                            resolved_commit=str(target_state.get("resolved_commit", "")),
                            resolved_tree=str(target_state.get("resolved_tree", "")),
                            phase_artifact_digests=phase_artifacts,
                            candidate_digest=str(digests["candidate"]),
                            acceptance_tests_digest=str(digests["acceptance-tests"]),
                            trusted_evidence_digests=validation_evidence,
                        )
                    except AcceptanceObligationError as exc:
                        raise RunStateError(
                            f"ledger entry {index} preview acceptance-obligation report is "
                            f"invalid: {exc}"
                        ) from exc
                elif destination is RunState.SPECIFICATION_DEFECT:
                    validation_evidence = {}

            if schema_version == RUN_SCHEMA_VERSION and index > 0:
                structural = {
                    "target",
                    "target-state",
                    "source",
                    "phase_artifacts",
                    "generation_artifacts",
                    TRANSITION_OBLIGATION_SET_KEY,
                    TRANSITION_OBLIGATION_REPORT_KEY,
                    ACCEPTANCE_OBLIGATION_CATALOG_STRUCTURAL_KEY,
                }
                transition_supplied = {
                    str(key): value for key, value in digests.items() if key not in structural
                }
                if destination is RunState.TARGET_RESOLVED:
                    transition_supplied["target-state"] = str(digests["target-state"])
                if destination is RunState.INTAKE:
                    transition_supplied["source"] = str(digests["source"])
                try:
                    expected_set, expected_report = derive_transition_obligations(
                        run_id=run_id,
                        generation=generation,
                        source=source_raw,
                        destination=str(destination),
                        prior_ledger_head=str(record.get("prev_hash", "")),
                        target_state_digest=target_state_digest,
                        target_state=target_state,
                        phase_artifact_digests=phase_artifacts,
                        acceptance_obligation_catalog_digest=(acceptance_obligation_catalog_digest),
                        supplied_artifact_digests=transition_supplied,
                        payload=payload_raw,
                        approved_candidate_digest=prior_approved_candidate,
                        recorded_at=stamp,
                        implementer_identity=str(record.get("implementer_identity", "")),
                        verifier_identity=str(record.get("verifier_identity", "")),
                        approver_identity=str(record.get("approver_identity", "")),
                    )
                    verify_retained_transition_obligations(
                        self._run_dir(run_id),
                        expected_set=expected_set,
                        expected_report=expected_report,
                        set_digest=str(digests.get(TRANSITION_OBLIGATION_SET_KEY, "")),
                        report_digest=str(digests.get(TRANSITION_OBLIGATION_REPORT_KEY, "")),
                    )
                except TransitionObligationError as exc:
                    raise RunStateError(
                        f"ledger entry {index} state-triggered obligations are invalid: {exc}"
                    ) from exc

            if index == 0:
                created_at = stamp
            updated_at = stamp
            current = destination
            prior = destination

        return RunProjection(
            run_id=run_id,
            state=current,
            target_digest=target_digest,
            source_digest=source_digest,
            target_state_digest=target_state_digest,
            target_state=target_state,
            generation=generation,
            phase_artifact_digests=phase_artifacts,
            ledger_head=str(entries[-1]["entry_hash"]),
            created_at=created_at,
            updated_at=updated_at,
            approved_candidate_digest=approved_candidate,
            acceptance_obligation_catalog_digest=acceptance_obligation_catalog_digest,
            generation_artifact_digests=generation_artifacts,
            build_attempt_count=build_attempt_count,
            build_attempt_limit=build_attempt_limit,
            schema_version=schema_version,
        )

    def _write_projection(self, projection: RunProjection) -> None:
        path = self._projection_path(projection.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".run-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(projection.to_dict(), handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
