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
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from factory_core.manifest import Ledger, LedgerEntry, SegregationPolicy

RUN_SCHEMA_VERSION = "factory-run/1"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RunState(StrEnum):
    """The only states an executable Factory run may occupy."""

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
    phase_artifact_digests: Mapping[str, str]
    ledger_head: str
    created_at: int
    updated_at: int
    approved_candidate_digest: str = ""
    schema_version: str = RUN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["phase_artifact_digests"] = dict(self.phase_artifact_digests)
        return body

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RunProjection:
        phase_raw = raw.get("phase_artifact_digests")
        return cls(
            run_id=str(raw.get("run_id", "")),
            state=str(raw.get("state", "")),
            target_digest=str(raw.get("target_digest", "")),
            source_digest=str(raw.get("source_digest", "")),
            phase_artifact_digests=(
                {str(key): str(value) for key, value in phase_raw.items()}
                if isinstance(phase_raw, Mapping)
                else {}
            ),
            ledger_head=str(raw.get("ledger_head", "")),
            created_at=_as_int(raw.get("created_at")),
            updated_at=_as_int(raw.get("updated_at")),
            approved_candidate_digest=str(raw.get("approved_candidate_digest", "")),
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
    digests: Mapping[str, Any], phase_key: str | None, *, context: str
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
    allowed = (
        {f"{phase_key}:{role}-receipt" for role in _RATIFICATION_RECEIPT_ROLES}
        if phase_key
        else set()
    )
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


def _required_phase_keys(
    state: RunState,
    payload: Mapping[str, Any],
) -> frozenset[str]:
    if state is RunState.INTAKE:
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
        source_digest: str,
        actor: str,
        artifact_digests: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
        approver_identity: str = "",
        policy: SegregationPolicy | None = None,
    ) -> RunProjection:
        """Create the immutable intake/genesis transition for a new run."""

        _require_digest(target_digest, "target_digest")
        _require_digest(source_digest, "source_digest")
        if not actor.strip():
            raise RunStateError("actor is required")
        supplied = dict(artifact_digests or {})
        for key, value in supplied.items():
            _require_digest(value, f"artifact_digests[{key!r}]")
        run_dir = self._run_dir(run_id)
        if run_dir.exists() and not run_dir.is_dir():
            raise RunStateError(f"run path is not a directory: {run_id}")
        if (
            (run_dir / "ledger.jsonl").exists()
            or (run_dir / "run.json").exists()
        ):
            raise RunStateError(f"run already exists: {run_id}")
        run_dir.mkdir(parents=True, exist_ok=True)
        created_at = self._clock()
        ledger = self._ledger(run_id)
        ledger.append(
            LedgerEntry(
                capability_id=run_id,
                from_state="",
                to_state=RunState.INTAKE,
                approver_identity=approver_identity,
                artifact_digests={
                    **supplied,
                    "target": target_digest,
                    "source": source_digest,
                    "phase_artifacts": {},
                },
                payload={
                    **dict(payload or {}),
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
        """Append one authorized state transition and refresh the checked projection."""

        current = self.load(run_id)
        try:
            destination = RunState(to_state)
            source = RunState(current.state)
        except ValueError as exc:
            raise RunStateError(f"unsupported run state: {exc}") from exc
        if destination not in ALLOWED_TRANSITIONS[source]:
            raise RunStateError(f"transition refused: {source} -> {destination}")
        if not actor.strip():
            raise RunStateError("actor is required")

        supplied = dict(artifact_digests or {})
        for key, value in supplied.items():
            _require_digest(value, f"artifact_digests[{key!r}]")
        phases = dict(current.phase_artifact_digests)
        transition_payload = dict(payload or {})
        phase_key = _PHASE_STATE_KEYS.get(destination)
        _require_receipts_belong_here(supplied, phase_key, context=str(destination))
        if phase_key:
            phase_digest = supplied.get(phase_key, "")
            if not phase_digest:
                raise RunStateError(
                    f"{destination} requires artifact digest {phase_key!r}"
                )
            _require_ratification_receipts(
                supplied,
                phase_key,
                context=str(destination),
                already_recorded=_recorded_receipt_digests(self._ledger(run_id).entries()),
            )
            phases[phase_key] = phase_digest

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

        now = self._clock()
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
                    "source": current.source_digest,
                    "phase_artifacts": phases,
                },
                payload=transition_payload,
                actor=actor.strip(),
                created_at=str(now),
            ),
            policy,
        )
        projection = self._derive(run_id)
        self._write_projection(projection)
        return projection

    def _derive(self, run_id: str) -> RunProjection:
        ledger = self._ledger(run_id)
        ok, detail = ledger.verify_chain()
        if not ok:
            raise RunStateError(f"run ledger verification failed: {detail}")
        entries = ledger.entries()
        if not entries:
            raise RunStateError(f"run does not exist or has no ledger entries: {run_id}")

        prior = ""
        target_digest = ""
        source_digest = ""
        approved_candidate = ""
        phase_artifacts: dict[str, str] = {}
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
            if index == 0:
                if source_raw or destination is not RunState.INTAKE:
                    raise RunStateError("run genesis must transition from empty to intake")
            else:
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
            if index == 0:
                target_digest = str(digests.get("target", ""))
                source_digest = str(digests.get("source", ""))
                _require_digest(target_digest, "target_digest")
                _require_digest(source_digest, "source_digest")
            elif (
                digests.get("target") != target_digest
                or digests.get("source") != source_digest
            ):
                raise RunStateError(f"ledger entry {index} changes the run subject")

            derived_phase_key = _PHASE_STATE_KEYS.get(destination)
            _require_receipts_belong_here(
                digests, derived_phase_key, context=f"ledger entry {index}"
            )
            if derived_phase_key:
                # Same reason as the anchor states below: the chain proves an entry was not
                # edited, not that it was written through `transition`. A direct append chains
                # validly and would otherwise project as a ratification with no receipts.
                _require_ratification_receipts(
                    digests,
                    derived_phase_key,
                    context=f"ledger entry {index} ratification",
                    already_recorded=recorded_receipts,
                )
                # The entry records the ratified artifact twice — at the top level and in the
                # phase map. `transition` writes one value into both; two records of the same
                # digest that disagree is inadmissible rather than a matter of which one wins.
                phases_declared = digests.get("phase_artifacts")
                if (
                    isinstance(phases_declared, Mapping)
                    and str(phases_declared.get(derived_phase_key, ""))
                    != str(digests.get(derived_phase_key, ""))
                ):
                    raise RunStateError(
                        f"ledger entry {index} disagrees with itself about the "
                        f"{derived_phase_key!r} artifact digest"
                    )
            # Accumulated by the same rule `transition` reads the ledger with, and for every entry
            # rather than only the ratifying ones, so the two paths cannot disagree about which
            # digests are spent.
            recorded_receipts |= _receipt_digests_in(digests)

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
            elif destination is RunState.PROMOTED:
                promoted = str(digests.get("promoted-artifact", ""))
                if promoted != approved_candidate:
                    raise RunStateError(
                        f"ledger entry {index} promotes a digest that was never approved"
                    )

            phases_raw = digests.get("phase_artifacts")
            if not isinstance(phases_raw, Mapping):
                raise RunStateError(f"ledger entry {index} has no phase artifact map")
            candidate_phases = {
                str(key): str(value) for key, value in phases_raw.items()
            }
            payload_raw = record.get("payload")
            if not isinstance(payload_raw, Mapping):
                raise RunStateError(f"ledger entry {index} has no payload object")
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

            stamp = _as_int(record.get("created_at"))
            if stamp <= 0:
                raise RunStateError(f"ledger entry {index} has no valid created_at")
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
            phase_artifact_digests=phase_artifacts,
            ledger_head=ledger.head_hash(),
            created_at=created_at,
            updated_at=updated_at,
            approved_candidate_digest=approved_candidate,
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
