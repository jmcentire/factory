"""Deterministic instruction selection and delivery contracts.

Instruction text is context, not product authority.  This module closes the composition
boundary anyway: it verifies the exact checkpoint-bound source bytes, derives one bounded
effective view, compiles one role-specific contract from canonical doctrine, and validates an
exact dispatch readback.  None of those records proves author identity, semantic comprehension,
or an effect; the existing phase, obligation, broker, and promotion controls remain authoritative.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.directive_scope import (
    DIRECTIVE_ROLES,
    DirectiveScopeError,
    directive_scope_applies,
    parse_directive_scope,
    valid_directive_run_id,
)
from factory_runtime.schema import DocumentValidationError, validate_document

GENESIS_HASH = "0" * 64
MAX_SOURCE_BYTES = 65_536
MAX_DIRECTIVES = 256
MAX_QUALIFIERS = 64
MAX_DIRECTIVE_BYTES = 16_384
MAX_CONTRACT_BYTES = 262_144
MAX_DOCTRINE_BYTES = 1_048_576

_HASH = re.compile(r"^[0-9a-f]{64}$")
_SIGNED_ID = re.compile(r"^D-[0-9]{4}$")
_PROVISIONAL_ID = re.compile(r"^P-[0-9]{4}$")
_ROLE_TITLES = {"coder": "Coder", "tester": "Tester", "validator": "Validator"}


class InstructionControlError(ValueError):
    """An instruction source or derived contract could not be admitted safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_document_bytes(document: Mapping[str, Any]) -> bytes:
    """Return the one byte representation used by capsules, prompts, and receipts."""

    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _entry_hash(body: Mapping[str, Any]) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: object, *, label: str) -> dt.datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise InstructionControlError("INVALID_TIMESTAMP", f"{label} has no bounded timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise InstructionControlError("INVALID_TIMESTAMP", f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise InstructionControlError("INVALID_TIMESTAMP", f"{label} timestamp must be UTC")
    return parsed


def _bounded_text(value: object, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise InstructionControlError("INVALID_DIRECTIVE", f"{label} must be text")
    if not allow_empty and not value.strip():
        raise InstructionControlError("INVALID_DIRECTIVE", f"{label} must not be empty")
    if len(value.encode("utf-8")) > MAX_DIRECTIVE_BYTES:
        raise InstructionControlError("OVERSIZED_DIRECTIVE", f"{label} exceeds its byte ceiling")
    return value


def _qualifiers(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_QUALIFIERS:
        raise InstructionControlError("INVALID_QUALIFIERS", f"{label} qualifiers are invalid")
    result = [_bounded_text(item, label=f"{label} qualifier") for item in value]
    if len(set(result)) != len(result):
        raise InstructionControlError("DUPLICATE_QUALIFIER", f"{label} repeats a qualifier")
    return result


def _scope_applies(scope: object, *, run_id: str, generation: int, role: str) -> bool:
    """Resolve the closed directive-scope grammar against one invocation.

    `global` applies everywhere and legacy `run` means every invocation in the externally bound
    run. Narrow scopes are canonical semicolon-separated key/value pairs in run, generation,
    role order, for example `run=R1;generation=2;role=coder`. Unknown keys, reordering, empty
    values, noncanonical integers, and unsupported roles refuse instead of becoming broad scope.
    """

    try:
        return directive_scope_applies(
            scope,
            run_id=run_id,
            generation=generation,
            role=role,
        )
    except DirectiveScopeError as exc:
        raise InstructionControlError("INVALID_SCOPE", str(exc)) from exc


def _parse_jsonl(raw: bytes, *, label: str) -> list[dict[str, Any]]:
    if len(raw) > MAX_SOURCE_BYTES:
        raise InstructionControlError(
            "OVERSIZED_SOURCE", f"{label} exceeds {MAX_SOURCE_BYTES} bytes"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstructionControlError("INVALID_SOURCE", f"{label} is not UTF-8") from exc
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InstructionControlError(
                "INVALID_SOURCE", f"{label} line {line_number} is not JSON"
            ) from exc
        if not isinstance(value, dict):
            raise InstructionControlError(
                "INVALID_SOURCE", f"{label} line {line_number} is not an object"
            )
        result.append(value)
        if len(result) > MAX_DIRECTIVES:
            raise InstructionControlError(
                "OVERSIZED_SOURCE", f"{label} exceeds {MAX_DIRECTIVES} entries"
            )
    return result


def _verify_hash_chain(
    entries: Sequence[dict[str, Any]],
    *,
    label: str,
    id_pattern: re.Pattern[str],
    prefix: str,
) -> None:
    previous = GENESIS_HASH
    seen: set[str] = set()
    for index, entry in enumerate(entries, 1):
        identifier = entry.get("id")
        expected_id = f"{prefix}-{index:04d}"
        if not isinstance(identifier, str) or not id_pattern.fullmatch(identifier):
            raise InstructionControlError("INVALID_DIRECTIVE_ID", f"{label} has an invalid id")
        if identifier != expected_id or identifier in seen:
            raise InstructionControlError(
                "NONCANONICAL_DIRECTIVE_ID", f"{label} id sequence is not canonical"
            )
        seen.add(identifier)
        if entry.get("prev_hash") != previous:
            raise InstructionControlError("BROKEN_DIRECTIVE_CHAIN", f"{identifier} chain is broken")
        claimed = entry.get("hash")
        if not isinstance(claimed, str) or not _HASH.fullmatch(claimed):
            raise InstructionControlError("INVALID_DIRECTIVE_HASH", f"{identifier} hash is invalid")
        body = {key: value for key, value in entry.items() if key != "hash"}
        if _entry_hash(body) != claimed:
            raise InstructionControlError("ALTERED_DIRECTIVE", f"{identifier} content is altered")
        previous = claimed


def _validate_provisional(entries: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    allowed = {
        "id",
        "ts",
        "scope",
        "text",
        "qualifiers",
        "cite",
        "expires",
        "prev_hash",
        "hash",
    }
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        identifier = str(entry.get("id", ""))
        if set(entry) != allowed:
            raise InstructionControlError(
                "INVALID_PROVISIONAL", f"{identifier or 'provisional entry'} has unknown fields"
            )
        _timestamp(entry["ts"], label=identifier)
        expires = _timestamp(entry["expires"], label=f"{identifier} expiry")
        created = _timestamp(entry["ts"], label=identifier)
        if expires <= created:
            raise InstructionControlError(
                "INVALID_PROVISIONAL", f"{identifier} does not expire after creation"
            )
        try:
            parse_directive_scope(entry["scope"])
        except DirectiveScopeError as exc:
            raise InstructionControlError("INVALID_SCOPE", str(exc)) from exc
        _bounded_text(entry["text"], label=f"{identifier} text")
        _bounded_text(entry["cite"], label=f"{identifier} citation")
        _qualifiers(entry["qualifiers"], label=identifier)
        result[identifier] = entry
    return result


def _validate_signed(
    entries: Sequence[dict[str, Any]],
    provisional: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, str], set[str]]:
    base_fields = {
        "id",
        "ts",
        "scope",
        "text",
        "qualifiers",
        "supersedes",
        "dispositions",
        "prev_hash",
        "hash",
    }
    by_id: dict[str, dict[str, Any]] = {}
    successor: dict[str, str] = {}
    settled: set[str] = set()
    for entry in entries:
        identifier = str(entry.get("id", ""))
        verdict_fields = {name for name in ("ratifies", "refuses") if name in entry}
        if len(verdict_fields) > 1 or set(entry) != base_fields | verdict_fields:
            raise InstructionControlError(
                "INVALID_DIRECTIVE", f"{identifier or 'directive'} has unknown fields"
            )
        _timestamp(entry["ts"], label=identifier)
        try:
            parse_directive_scope(entry["scope"])
        except DirectiveScopeError as exc:
            raise InstructionControlError("INVALID_SCOPE", str(exc)) from exc
        _bounded_text(entry["text"], label=f"{identifier} text")
        qualifiers = _qualifiers(entry["qualifiers"], label=identifier)
        parent_id = entry["supersedes"]
        dispositions = entry["dispositions"]
        if parent_id is None:
            if dispositions is not None:
                raise InstructionControlError(
                    "INVALID_SUPERSESSION", f"{identifier} has dispositions without a parent"
                )
        else:
            if verdict_fields or not isinstance(parent_id, str) or parent_id not in by_id:
                raise InstructionControlError(
                    "INVALID_SUPERSESSION", f"{identifier} supersedes no earlier directive"
                )
            if parent_id in successor:
                raise InstructionControlError(
                    "DIAMOND_SUPERSESSION", f"{parent_id} has multiple successors"
                )
            if not isinstance(dispositions, dict):
                raise InstructionControlError(
                    "INVALID_SUPERSESSION", f"{identifier} has no qualifier dispositions"
                )
            if entry["scope"] != by_id[parent_id]["scope"]:
                raise InstructionControlError(
                    "INVALID_SUPERSESSION",
                    f"{identifier} changes scope while superseding {parent_id}",
                )
            parent_qualifiers = list(by_id[parent_id]["qualifiers"])
            if set(dispositions) != set(parent_qualifiers):
                raise InstructionControlError(
                    "INVALID_SUPERSESSION", f"{identifier} does not disposition every qualifier"
                )
            carried: list[str] = []
            for qualifier in parent_qualifiers:
                disposition = dispositions[qualifier]
                if not isinstance(disposition, dict) or set(disposition) != {"action", "new"}:
                    raise InstructionControlError(
                        "INVALID_SUPERSESSION", f"{identifier} has an invalid disposition"
                    )
                action = disposition["action"]
                replacement = disposition["new"]
                if action not in {"kept", "dropped", "modified"}:
                    raise InstructionControlError(
                        "INVALID_SUPERSESSION", f"{identifier} has an invalid disposition action"
                    )
                if action == "kept":
                    if replacement is not None:
                        raise InstructionControlError(
                            "INVALID_SUPERSESSION", f"{identifier} kept qualifier has replacement"
                        )
                    carried.append(qualifier)
                elif action == "dropped":
                    if replacement is not None:
                        raise InstructionControlError(
                            "INVALID_SUPERSESSION",
                            f"{identifier} dropped qualifier has replacement",
                        )
                else:
                    carried.append(
                        _bounded_text(
                            replacement,
                            label=f"{identifier} modified qualifier",
                        )
                    )
            if any(value not in qualifiers for value in carried):
                raise InstructionControlError(
                    "INVALID_SUPERSESSION", f"{identifier} silently loses a carried qualifier"
                )
            successor[parent_id] = identifier
        for verdict in verdict_fields:
            reference = entry[verdict]
            if not isinstance(reference, dict) or set(reference) != {"id", "hash", "cite"}:
                raise InstructionControlError(
                    "INVALID_SETTLEMENT", f"{identifier} has an invalid {verdict} reference"
                )
            provisional_id = reference["id"]
            source = provisional.get(provisional_id) if isinstance(provisional_id, str) else None
            if source is None or provisional_id in settled:
                raise InstructionControlError(
                    "INVALID_SETTLEMENT", f"{identifier} settles no unique provisional entry"
                )
            if reference["hash"] != source["hash"] or reference["cite"] != source["cite"]:
                raise InstructionControlError(
                    "INVALID_SETTLEMENT", f"{identifier} settlement source differs"
                )
            if entry["qualifiers"] != source["qualifiers"]:
                raise InstructionControlError(
                    "INVALID_SETTLEMENT", f"{identifier} settlement changes qualifiers"
                )
            if entry["scope"] != source["scope"]:
                raise InstructionControlError(
                    "INVALID_SETTLEMENT", f"{identifier} settlement changes scope"
                )
            settled.add(provisional_id)
        by_id[identifier] = entry
    return successor, settled


def _head(entries: Sequence[Mapping[str, Any]]) -> str:
    value = str(entries[-1]["hash"]) if entries else GENESIS_HASH
    return f"sha256:{value}"


def _validated_sources(
    ledger_bytes: bytes,
    provisional_bytes: bytes,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, str],
    set[str],
]:
    signed_entries = _parse_jsonl(ledger_bytes, label="directive ledger")
    provisional_entries = _parse_jsonl(provisional_bytes, label="provisional directive chain")
    _verify_hash_chain(
        signed_entries,
        label="directive ledger",
        id_pattern=_SIGNED_ID,
        prefix="D",
    )
    _verify_hash_chain(
        provisional_entries,
        label="provisional directive chain",
        id_pattern=_PROVISIONAL_ID,
        prefix="P",
    )
    provisional = _validate_provisional(provisional_entries)
    successor, settled = _validate_signed(signed_entries, provisional)
    return signed_entries, provisional_entries, provisional, successor, settled


def validate_directive_sources(*, ledger_bytes: bytes, provisional_bytes: bytes) -> None:
    """Validate complete prospective chains without selecting an invocation."""

    _validated_sources(ledger_bytes, provisional_bytes)


def derive_effective_directive_contract(
    *,
    ledger_bytes: bytes,
    provisional_bytes: bytes,
    run_id: str,
    generation: int,
    role: str,
    evaluated_at: int,
) -> dict[str, Any]:
    """Derive the exact active directives or refuse lower-trust ambiguity.

    The external checkpoint proves which source bytes the run admitted. It does not, by
    itself, prove the human identity that authored those bytes; signer authentication remains
    a separate ceremony/boundary and must not be implied by this derived record.
    """

    if not valid_directive_run_id(run_id):
        raise InstructionControlError("INVALID_SCOPE", "run id is invalid")
    if generation < 1 or role not in DIRECTIVE_ROLES or evaluated_at < 1:
        raise InstructionControlError("INVALID_SCOPE", "instruction contract scope is invalid")
    signed_entries, provisional_entries, provisional, successor, settled = _validated_sources(
        ledger_bytes,
        provisional_bytes,
    )
    at = dt.datetime.fromtimestamp(evaluated_at, tz=dt.UTC)
    provisional_applies = {
        identifier: _scope_applies(
            entry["scope"], run_id=run_id, generation=generation, role=role
        )
        for identifier, entry in provisional.items()
    }
    signed_applies = {
        str(entry["id"]): _scope_applies(
            entry["scope"], run_id=run_id, generation=generation, role=role
        )
        for entry in signed_entries
    }
    live_provisional = [
        identifier
        for identifier, entry in provisional.items()
        if (
            identifier not in settled
            and provisional_applies[identifier]
            and _timestamp(entry["expires"], label=identifier) > at
        )
    ]
    if live_provisional:
        raise InstructionControlError(
            "UNSETTLED_PROVISIONAL",
            "unsettled provisional directives require human ratification/refusal: "
            + ", ".join(live_provisional),
        )
    directives: list[dict[str, Any]] = []
    for entry in signed_entries:
        identifier = str(entry["id"])
        if identifier in successor or "refuses" in entry or not signed_applies[identifier]:
            continue
        directives.append(
            {
                "directive_id": identifier,
                "scope": entry["scope"],
                "text": entry["text"],
                "qualifiers": list(entry["qualifiers"]),
                "entry_digest": f"sha256:{entry['hash']}",
                "source_class": "externally-checkpoint-bound-directive-ledger",
            }
        )
    document = {
        "schema_version": "factory-effective-directive-contract/1",
        "run_id": run_id,
        "generation": generation,
        "role": role,
        "evaluated_at": evaluated_at,
        "selection_policy": (
            "applicable-unsuperseded-checkpoint-bound-entries-unsettled-provisional-blocks"
        ),
        "verification_mode": "externally-checkpoint-bound-hash-chain",
        "ledger": {
            "source_digest": digest_bytes(ledger_bytes),
            "head": _head(signed_entries),
            "entry_count": len(signed_entries),
        },
        "provisional": {
            "source_digest": digest_bytes(provisional_bytes),
            "head": _head(provisional_entries),
            "entry_count": len(provisional_entries),
            "live_unsettled_count": 0,
        },
        "directives": directives,
    }
    try:
        validate_document("effective-directive-contract", document)
    except DocumentValidationError as exc:
        raise InstructionControlError("INVALID_CONTRACT", str(exc)) from exc
    if len(canonical_document_bytes(document)) > MAX_CONTRACT_BYTES:
        raise InstructionControlError("OVERSIZED_CONTRACT", "instruction contract is oversized")
    return document


def verify_effective_directive_contract(
    document: Mapping[str, Any],
    *,
    ledger_bytes: bytes,
    provisional_bytes: bytes,
    expected_run_id: str,
    expected_generation: int,
    expected_role: str,
    current_time: int | None = None,
) -> None:
    try:
        snapshot = json.loads(json.dumps(document, sort_keys=True, separators=(",", ":")))
        evaluated_at = snapshot["evaluated_at"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InstructionControlError(
            "INVALID_CONTRACT", "instruction contract is invalid"
        ) from exc
    if isinstance(evaluated_at, bool) or not isinstance(evaluated_at, int):
        raise InstructionControlError("INVALID_CONTRACT", "instruction evaluation time is invalid")
    if current_time is not None and current_time < evaluated_at:
        raise InstructionControlError(
            "FUTURE_CONTRACT",
            "instruction contract evaluation time is in the future",
        )
    expected = derive_effective_directive_contract(
        ledger_bytes=ledger_bytes,
        provisional_bytes=provisional_bytes,
        run_id=expected_run_id,
        generation=expected_generation,
        role=expected_role,
        evaluated_at=evaluated_at,
    )
    if snapshot != expected:
        raise InstructionControlError(
            "CONTRACT_MISMATCH", "instruction contract differs from exact admitted sources"
        )


def compile_role_contract(*, doctrine_bytes: bytes, role: str) -> dict[str, Any]:
    """Compile the shared foundation plus exactly one canonical role section."""

    title = _ROLE_TITLES.get(role)
    if title is None:
        raise InstructionControlError("INVALID_ROLE", f"unsupported role contract: {role!r}")
    if len(doctrine_bytes) > MAX_DOCTRINE_BYTES:
        raise InstructionControlError("OVERSIZED_SOURCE", "role doctrine source is oversized")
    try:
        doctrine = doctrine_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstructionControlError(
            "INVALID_ROLE_CONTRACT", "role doctrine is not UTF-8"
        ) from exc
    shared_marker = "## Shared foundation"
    role_markers = {
        name: f"## Directive — {role_title}" for name, role_title in _ROLE_TITLES.items()
    }
    if doctrine.count(shared_marker) != 1 or any(
        doctrine.count(marker) != 1 for marker in role_markers.values()
    ):
        raise InstructionControlError(
            "INVALID_ROLE_CONTRACT", "canonical role doctrine headings are missing or duplicated"
        )
    shared_start = doctrine.index(shared_marker)
    first_role = min(doctrine.index(marker) for marker in role_markers.values())
    role_start = doctrine.index(role_markers[role])
    later = [
        match.start()
        for match in re.finditer(r"^## ", doctrine, flags=re.MULTILINE)
        if match.start() > role_start
    ]
    role_end = min(later) if later else len(doctrine)
    if not (shared_start < first_role <= role_start < role_end):
        raise InstructionControlError("INVALID_ROLE_CONTRACT", "role doctrine order is invalid")
    instructions = (
        f"# Factory role contract — {title}\n\n"
        + doctrine[shared_start:first_role].strip()
        + "\n\n"
        + doctrine[role_start:role_end].strip()
        + "\n"
    )
    instructions_bytes = instructions.encode("utf-8")
    document = {
        "schema_version": "factory-role-contract/1",
        "compiler_version": "factory-role-contract-compiler/1",
        "role": role,
        "source_digest": digest_bytes(doctrine_bytes),
        "instructions_digest": digest_bytes(instructions_bytes),
        "instructions": instructions,
    }
    try:
        validate_document("role-contract", document)
    except DocumentValidationError as exc:
        raise InstructionControlError("INVALID_ROLE_CONTRACT", str(exc)) from exc
    if len(canonical_document_bytes(document)) > MAX_CONTRACT_BYTES:
        raise InstructionControlError("OVERSIZED_CONTRACT", "role contract is oversized")
    return document


def verify_role_contract(
    document: Mapping[str, Any], *, doctrine_bytes: bytes, expected_role: str
) -> None:
    try:
        snapshot = json.loads(json.dumps(document, sort_keys=True, separators=(",", ":")))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InstructionControlError("INVALID_ROLE_CONTRACT", "role contract is invalid") from exc
    if snapshot != compile_role_contract(doctrine_bytes=doctrine_bytes, role=expected_role):
        raise InstructionControlError(
            "ROLE_CONTRACT_MISMATCH", "role contract differs from canonical doctrine"
        )


def validate_directive_readback(
    document: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    expected_run_id: str,
    expected_generation: int,
    expected_role: str,
) -> None:
    """Validate attribution and exact membership, never semantic understanding."""

    try:
        snapshot = json.loads(json.dumps(document, sort_keys=True, separators=(",", ":")))
        validate_document("directive-readback", snapshot)
    except (TypeError, ValueError, json.JSONDecodeError, DocumentValidationError) as exc:
        raise InstructionControlError(
            "INVALID_READBACK", f"directive readback is invalid: {exc}"
        ) from exc
    expected_fields = {
        "run_id": expected_run_id,
        "generation": expected_generation,
        "role": expected_role,
        "effective_directive_contract_digest": digest_obj(dict(contract)),
    }
    for field, expected in expected_fields.items():
        if snapshot.get(field) != expected:
            raise InstructionControlError("READBACK_SCOPE_MISMATCH", f"readback has wrong {field}")
    if snapshot["task_interpretation"]["ambiguity"] != "none":
        raise InstructionControlError(
            "READBACK_AMBIGUOUS", "the dispatch interpretation remains ambiguous"
        )
    source_directives = list(contract.get("directives", []))
    readbacks = list(snapshot.get("directives", []))
    expected_ids = [item["directive_id"] for item in source_directives]
    actual_ids = [item["directive_id"] for item in readbacks]
    if actual_ids != expected_ids:
        raise InstructionControlError(
            "READBACK_MEMBERSHIP_MISMATCH", "readback does not cover the exact directive set"
        )
    for source, readback in zip(source_directives, readbacks, strict=True):
        if readback["source_quote"] != source["text"]:
            raise InstructionControlError(
                "READBACK_QUOTE_MISMATCH", f"readback quote differs for {source['directive_id']}"
            )
        if readback["ambiguity"] != "none":
            raise InstructionControlError(
                "READBACK_AMBIGUOUS", f"{source['directive_id']} remains ambiguous"
            )
        qualifier_readbacks = list(readback["qualifier_readback"])
        source_qualifiers = list(source["qualifiers"])
        if [item["source_quote"] for item in qualifier_readbacks] != source_qualifiers:
            raise InstructionControlError(
                "READBACK_QUALIFIER_MISMATCH",
                f"readback qualifiers differ for {source['directive_id']}",
            )
        if any(item["ambiguity"] != "none" for item in qualifier_readbacks):
            raise InstructionControlError(
                "READBACK_AMBIGUOUS",
                f"a qualifier for {source['directive_id']} remains ambiguous",
            )


def validate_lane_dispatch(
    document: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    expected_run_id: str,
    expected_generation: int,
    expected_role: str,
) -> tuple[bytes, dict[str, Any]]:
    """Admit a structured operator dispatch and construct its bound readback evidence."""

    try:
        snapshot = json.loads(json.dumps(document, sort_keys=True, separators=(",", ":")))
        validate_document("lane-dispatch", snapshot)
    except (TypeError, ValueError, json.JSONDecodeError, DocumentValidationError) as exc:
        raise InstructionControlError(
            "INVALID_LANE_DISPATCH", f"lane dispatch is invalid: {exc}"
        ) from exc
    expected_fields = {
        "run_id": expected_run_id,
        "generation": expected_generation,
        "role": expected_role,
    }
    for field, expected in expected_fields.items():
        if snapshot.get(field) != expected:
            raise InstructionControlError(
                "LANE_DISPATCH_SCOPE_MISMATCH", f"lane dispatch has wrong {field}"
            )
    readback = {
        "schema_version": "factory-directive-readback/1",
        "run_id": expected_run_id,
        "generation": expected_generation,
        "role": expected_role,
        "effective_directive_contract_digest": digest_obj(dict(contract)),
        "semantic_clearance": False,
        "task_interpretation": dict(snapshot["interpretation"]),
        "directives": [dict(item) for item in snapshot["directive_readback"]],
    }
    validate_directive_readback(
        readback,
        contract=contract,
        expected_run_id=expected_run_id,
        expected_generation=expected_generation,
        expected_role=expected_role,
    )
    task = str(snapshot["task"]).encode("utf-8")
    if len(task) > 2_097_152:
        raise InstructionControlError("OVERSIZED_TASK", "lane task exceeds its byte ceiling")
    return task, readback
