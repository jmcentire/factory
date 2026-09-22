"""TRANSITION_ADMISSION — declarative admission data both state paths consume.

Plan 4.1c, built incrementally: each admission axis moves out of the write-path
elif chain and its derive-side twin INTO one schema-version-keyed row here, and
both paths consume the same row — deleting the two-path-drift class one axis at
a time instead of betting the whole state machine on a single rewrite.

The stopping rule that keeps the drift class dead: released versions' rows are
FROZEN — never edited — and a digest pin over the row data turns any edit red;
current-version behavior changes land as a new keyed row.

First migrated axis: authority-nonce counting. The two inline computations had
already drifted when this module was extracted (the write path counted INTAKE
only; the derive path counted TARGET_RESOLUTION_AUTHORIZED or INTAKE) — the
exact defect this table exists to close.

Second migrated axis: transition activations — the changed-existing-tests
extraction with its only-when-building refusal, the first-build catalog
activation predicate, and the ratified-artifact-key assembly, which both paths
previously computed as verbatim twins. This axis is version-uniform GIVEN the
obligation-replay membership fact, which keeps its single existing authority
(``state.OBLIGATION_REPLAY_RUN_SCHEMA_VERSIONS``) and enters as a caller
argument — no per-version row field, so the frozen-row pins are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Ratification artifact keys both state paths assemble activation sets from.
#: Defined here (the shared admission module) so neither path owns the other's
#: copy; ``state`` re-exports them for its existing consumers.
ACCEPTANCE_OBLIGATION_CATALOG_KEY = "acceptance-obligation-catalog"
TEST_CHANGE_AUTHORIZATION_KEY = "test-change-authorization"

#: Third migrated axis: the artifact-key membership tuples both state paths
#: previously enumerated as verbatim inline twins. The write-side VALIDATING
#: supplied-set (the four execution keys alone) is NOT one of them — what must
#: be newly supplied and what the entry must carry are different facts, and it
#: stays in ``state``.
ACCEPTANCE_OBLIGATION_REPORT_KEY = "acceptance-obligation-report"
VALIDATOR_EXECUTION_ARTIFACT_KEYS: tuple[str, ...] = (
    "validator-execution-manifest",
    "validator-execution-configuration",
    "validator-execution-environment",
    "validator-execution-snapshot",
)
#: The immutable validation subject frozen at VALIDATING — the entry-carried
#: membership the derive path requires and the write path trusts at PREVIEW.
VALIDATION_SUBJECT_KEYS: tuple[str, ...] = (
    "candidate",
    "acceptance-tests",
    "coder-output-snapshot",
    "tester-output-snapshot",
    *VALIDATOR_EXECUTION_ARTIFACT_KEYS,
)
#: Every digest a PREVIEW transition must carry.
PREVIEW_REQUIRED_ARTIFACT_KEYS: tuple[str, ...] = (
    "candidate",
    "acceptance-tests",
    ACCEPTANCE_OBLIGATION_REPORT_KEY,
    "validator-review-subject",
    "validator-adversarial-review",
    "base-source-snapshot",
    "candidate-change-set",
    "validator-review-authority-context",
    "validator-review-observations-source",
    *VALIDATOR_EXECUTION_ARTIFACT_KEYS,
    "evidence-bundle",
    "evidence-envelope",
)
#: Keys that may not change between immutable validation and PREVIEW.
IMMUTABLE_AFTER_VALIDATION_KEYS: tuple[str, ...] = (
    "candidate",
    "acceptance-tests",
    *VALIDATOR_EXECUTION_ARTIFACT_KEYS,
)


class AdmissionRefusal(ValueError):
    """An admission axis refused the transition; callers rewrap as their error."""

#: Destinations whose transition consumes one intake/resolution authority nonce.
_AUTHORITY_NONCE_BASE_STATES = frozenset(
    {"target-resolution-authorized", "intake"}
)


@dataclass(frozen=True)
class _NonceAdmissionRow:
    """One schema version's authority-nonce counting rule (frozen once released)."""

    base_states: frozenset[str]
    #: nonce generations tolerated per phase ratification: single-seat records 1,
    #: dual-ratified history recorded 2, pre-nonce legacy recorded 0.
    phase_extras: tuple[int, ...]
    #: extra nonces tolerated per activation (catalog / test-change) from
    #: dual-ratified history's validator nonce.
    activation_dual_extra: bool
    #: 4.1's rule for LLM entry rows (a CONTRACT, not a count): a row that admits
    #: externally produced bytes MUST name its mechanical validator, and the named
    #: validator must resolve to a callable in ADMISSION_VALIDATORS — enforced by
    #: a forcing test that enumerates every row. No current row admits bytes; the
    #: contract is mechanical from day one so the first byte-admitting row cannot
    #: land unvalidated.
    admits_external_bytes: bool = False
    named_validator: str = ""


TRANSITION_ADMISSION: dict[str, _NonceAdmissionRow] = {
    # factory-run/5 — current. 4.1b single-seat authority with all three retained
    # ledger generations tolerated.
    "factory-run/5": _NonceAdmissionRow(
        base_states=_AUTHORITY_NONCE_BASE_STATES,
        phase_extras=(0, 1, 2),
        activation_dual_extra=True,
    ),
    # factory-run/4 — released; FROZEN. v4 ledgers replay under exactly these
    # rules forever.
    "factory-run/4": _NonceAdmissionRow(
        base_states=_AUTHORITY_NONCE_BASE_STATES,
        phase_extras=(0, 1, 2),
        activation_dual_extra=True,
    ),
}


def allowed_authority_nonce_counts(
    *,
    schema_version: str,
    destination: str,
    phase_key: bool,
    catalog_activation: bool,
    test_change_activation: bool,
) -> frozenset[int]:
    """The one answer both the write path and the derive path consume.

    An unknown schema version fails closed by resolving to the current row —
    the caller has already refused unknown versions before admission counting.
    """

    row = TRANSITION_ADMISSION.get(schema_version, TRANSITION_ADMISSION["factory-run/5"])
    base = (
        (1 if destination in row.base_states else 0)
        + (1 if catalog_activation else 0)
        + (1 if test_change_activation else 0)
    )
    allowed = {base}
    if row.activation_dual_extra:
        dual_extras = int(bool(catalog_activation)) + int(bool(test_change_activation))
        for extra in range(1, dual_extras + 1):
            allowed.add(base + extra)
    if phase_key:
        for extra in row.phase_extras:
            allowed.add(base + extra)
    return frozenset(allowed)


@dataclass(frozen=True)
class _TransitionActivations:
    """The one activation answer both state paths consume (second axis)."""

    catalog_activation: bool
    test_change_activation: bool
    ratified_artifact_keys: frozenset[str]


def transition_activations(
    *,
    destination: str,
    phase_key: str | None,
    changed_existing_tests_raw: object,
    catalog_digest_recorded: bool,
    obligation_replay: bool,
    context: str = "",
) -> _TransitionActivations:
    """Derive the transition's activation facts, refusing malformed shapes.

    ``obligation_replay`` is the caller-resolved schema-version membership in
    the obligation-replay contract (single authority in ``state``); versions
    outside it never activate a catalog. ``context`` prefixes refusal messages
    so a ledger-replay refusal names its entry.
    """

    if not isinstance(changed_existing_tests_raw, list):
        raise AdmissionRefusal(f"{context}changed_existing_tests must be an exact array")
    test_change_activation = bool(
        [str(test_id) for test_id in changed_existing_tests_raw]
    )
    if test_change_activation and destination != "building":
        raise AdmissionRefusal(
            f"{context}test expectation changes may be authorized only when "
            "entering building"
        )
    catalog_activation = (
        obligation_replay and destination == "building" and not catalog_digest_recorded
    )
    return _TransitionActivations(
        catalog_activation=catalog_activation,
        test_change_activation=test_change_activation,
        ratified_artifact_keys=frozenset(
            key
            for key in (
                phase_key,
                ACCEPTANCE_OBLIGATION_CATALOG_KEY if catalog_activation else None,
                TEST_CHANGE_AUTHORIZATION_KEY if test_change_activation else None,
            )
            if key is not None
        ),
    )


#: The registry a byte-admitting row's ``named_validator`` must resolve into.
#: Populated as byte-admitting rows migrate into the table; a name absent here
#: while a row claims it is a red forcing test, never a silent pass.
ADMISSION_VALIDATORS: dict[str, object] = {}


@dataclass(frozen=True)
class _WalkedDestination:
    """One authority destination the run must traverse, derived from the row."""

    destination: str
    named_validator: str


def authority_destination_walk(
    *, schema_version: str, ratification_destinations: tuple[str, ...]
) -> tuple[_WalkedDestination, ...]:
    """Derive the run's authority destinations from the admission row.

    Re-bases Phase 1's obligation walk (plan cross-axis resolution 4). The
    walk's MEMBERSHIP is the fact it owns: every destination it returns needs
    a resolvable human authority (base states off the row consume the signed
    intake/resolution authority; ratifications and the building activations
    require ratification receipts), so the preflight's zero-humans NO can
    enumerate what is unreachable without re-asserting the list inline. The
    per-destination receipt and nonce ENFORCEMENT stays where it always was —
    the obligation layer and ``allowed_authority_nonce_counts`` — and is
    deliberately not restated here as flags (round-8 8-3: restated flags were
    asserted-but-unconsumed literals, a third authority for one fact).
    ``named_validator`` is read off the row's byte-admission contract.
    """

    row = TRANSITION_ADMISSION.get(schema_version, TRANSITION_ADMISSION["factory-run/5"])
    validator = row.named_validator if row.admits_external_bytes else ""
    destinations = (
        *sorted(row.base_states),
        *ratification_destinations,
        # first-build catalog activation and test-change activation both
        # ratify at the building destination.
        "building",
    )
    return tuple(
        _WalkedDestination(destination=destination, named_validator=validator)
        for destination in destinations
    )
