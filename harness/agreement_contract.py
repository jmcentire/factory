#!/usr/bin/env python3
"""Derive and verify cross-path agreement obligations for a Factory run.

The contract is testing IR, not a fourth intent authority.  It closes membership over the
explicit Product-requirement region families named at ignition, derives single/cross-path from a
retained participant inventory, and renders one deterministic register into the ratified Testing
and Monitoring Strategy.  Phase-C evidence is exact-subject: mismatch witnesses bind the
candidate plus the local-suite and agreement-oracle byte digests they compared.

Mechanical checks cannot decide that a participant inventory is semantically complete.  A
bounded-manual inventory is therefore represented as a weaker disclosed closure class and may
never clear a Critical requirement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CONTRACT_SCHEMA = "factory-agreement-contract/1"
INVENTORY_SCHEMA = "factory-agreement-participant-inventory/1"
EVIDENCE_SCHEMA = "factory-agreement-evidence/1"
WITNESS_SCHEMA = "factory-agreement-witness/1"
STRUCTURAL_REVIEW_SCHEMA = "factory-agreement-structural-review/1"
BEGIN = "<!-- FACTORY-AGREEMENT-CONTRACT:BEGIN -->"
END = "<!-- FACTORY-AGREEMENT-CONTRACT:END -->"
FAMILY_ORDER = ("authored-product", "run-guidance")
AXES = ("version-skew", "data-at-rest", "retry", "duplication", "ordering", "error-taxonomy")
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_AUTHORED_REQUIREMENT = re.compile(r"^- \*\*(R[0-9]+\.[0-9]+)\*\*")
_GUIDANCE_REQUIREMENT = re.compile(r"^- \*\*(G-[A-Za-z0-9][A-Za-z0-9._-]{0,123})\*\*")
_ANY_FACTORY_BEGIN = re.compile(r"^<!-- FACTORY-[A-Z0-9-]+:BEGIN -->$")
_ANY_FACTORY_END = re.compile(r"^<!-- FACTORY-[A-Z0-9-]+:END -->$")


class AgreementContractError(RuntimeError):
    """Agreement planning or evidence did not satisfy the closed contract."""


@dataclass(frozen=True)
class RequirementRegion:
    family: str
    region_digest: str
    requirements: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AgreementPlan:
    run_id: str
    families: tuple[str, ...]
    regions: tuple[RequirementRegion, ...]
    inventory: Mapping[str, Any]
    inventory_digest: str
    contract: Mapping[str, Any]
    contract_digest: str
    inventory_items: Mapping[str, Mapping[str, Any]]
    contract_entries: Mapping[str, Mapping[str, Any]]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular(path: pathlib.Path, *, ceiling: int, allow_empty: bool = False) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AgreementContractError(f"input is not a regular file: {path}")
        if before.st_size > ceiling or (before.st_size == 0 and not allow_empty):
            raise AgreementContractError(f"input is empty or exceeds its byte ceiling: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, ceiling + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > ceiling:
                raise AgreementContractError(f"input exceeds its byte ceiling: {path}")
        after = os.fstat(descriptor)
        installed = os.lstat(path)
        if (
            _file_identity(before) != _file_identity(after)
            or stat.S_ISLNK(installed.st_mode)
            or (installed.st_dev, installed.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise AgreementContractError(f"input changed while read: {path}")
        return b"".join(chunks)
    except OSError as exc:
        raise AgreementContractError(f"cannot read regular input {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_file(path: pathlib.Path, *, canonical: bool = True) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, ceiling=MAX_JSON_BYTES)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgreementContractError(f"invalid JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AgreementContractError(f"JSON input must be an object: {path}")
    if canonical and raw != _canonical(value):
        raise AgreementContractError(f"JSON input is not canonical: {path}")
    return value, raw


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise AgreementContractError(
            f"{label} fields differ: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )


def _text(value: object, label: str, *, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise AgreementContractError(f"{label} must be bounded non-empty text")
    if "\x00" in value:
        raise AgreementContractError(f"{label} contains NUL")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise AgreementContractError(f"{label} is not a safe identifier")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise AgreementContractError(f"{label} is not a canonical SHA-256 address")
    return value


def _closed_relative(root: pathlib.Path, value: object, label: str) -> pathlib.Path:
    text = _text(value, label, maximum=1024)
    relative = pathlib.PurePosixPath(text)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise AgreementContractError(f"{label} must be a closed relative path")
    candidate = root.joinpath(*relative.parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AgreementContractError(f"{label} cannot be resolved: {exc}") from exc
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise AgreementContractError(f"{label} escapes the run root")
    current = resolved_root
    for part in resolved.relative_to(resolved_root).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise AgreementContractError(f"{label} crosses a symlink")
    return resolved


def _read_harness(root: pathlib.Path) -> dict[str, Any] | None:
    path = root / "harness.json"
    if not path.exists() and not path.is_symlink():
        return None
    value, _raw = _json_file(path, canonical=False)
    return value


def required_configuration(root: pathlib.Path) -> tuple[str, tuple[str, ...]] | None:
    """Return the version/families for a new run, or ``None`` for a legacy run."""

    harness = _read_harness(root)
    if harness is None or "agreement_contract_version" not in harness:
        return None
    version = harness.get("agreement_contract_version")
    if version != CONTRACT_SCHEMA:
        raise AgreementContractError(f"unsupported agreement contract version: {version!r}")
    families_raw = harness.get("agreement_requirement_region_families")
    if not isinstance(families_raw, list) or not families_raw:
        raise AgreementContractError("agreement requirement-region families are missing")
    families = tuple(families_raw)
    if (
        any(not isinstance(item, str) or item not in FAMILY_ORDER for item in families)
        or len(families) != len(set(families))
        or families != tuple(item for item in FAMILY_ORDER if item in families)
    ):
        raise AgreementContractError("agreement requirement-region families are not canonical")
    return version, families


def _marker_membership(lines: Sequence[str]) -> tuple[set[int], set[int]]:
    generated: set[int] = set()
    guidance: set[int] = set()
    stack: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        if _ANY_FACTORY_BEGIN.fullmatch(line):
            stack.append((line, index))
        if stack:
            generated.add(index)
            if any(marker == "<!-- FACTORY-RUN-GUIDANCE:BEGIN -->" for marker, _ in stack):
                guidance.add(index)
        if _ANY_FACTORY_END.fullmatch(line):
            if not stack:
                raise AgreementContractError(
                    "Product Specification has an unmatched generated-region end"
                )
            begin, _start = stack.pop()
            expected = begin.replace(":BEGIN -->", ":END -->")
            if line != expected:
                raise AgreementContractError(
                    "Product Specification generated regions cross or mismatch"
                )
    if stack:
        raise AgreementContractError("Product Specification has an unterminated generated region")
    return generated, guidance


def _requirement_blocks(
    lines: Sequence[str],
    *,
    indices: set[int],
    pattern: re.Pattern[str],
) -> tuple[tuple[str, str], ...]:
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = pattern.match(line) if index in indices else None
        if match:
            starts.append((index, match.group(1)))
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, (start, requirement_id) in enumerate(starts):
        if requirement_id in seen:
            raise AgreementContractError(f"Product Specification repeats {requirement_id}")
        seen.add(requirement_id)
        end = starts[number + 1][0] if number + 1 < len(starts) else len(lines)
        for index in range(start + 1, end):
            if (
                lines[index].startswith("## ")
                or _ANY_FACTORY_BEGIN.fullmatch(lines[index])
                or _ANY_FACTORY_END.fullmatch(lines[index])
            ):
                end = index
                break
        body = "\n".join(lines[start:end]).rstrip()
        result.append((requirement_id, body))
    return tuple(result)


def derive_regions(
    spec_path: pathlib.Path, families: Sequence[str]
) -> tuple[RequirementRegion, ...]:
    try:
        text = _read_regular(spec_path, ceiling=MAX_ARTIFACT_BYTES).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgreementContractError("Product Specification is not UTF-8") from exc
    lines = text.splitlines()
    generated, guidance = _marker_membership(lines)
    all_indices = set(range(len(lines)))
    regions: list[RequirementRegion] = []
    for family in families:
        if family == "authored-product":
            requirements = _requirement_blocks(
                lines,
                indices=all_indices - generated,
                pattern=_AUTHORED_REQUIREMENT,
            )
        elif family == "run-guidance":
            requirements = _requirement_blocks(
                lines,
                indices=guidance,
                pattern=_GUIDANCE_REQUIREMENT,
            )
        else:  # guarded by required_configuration; keep this function safe in direct use.
            raise AgreementContractError(f"unsupported requirement-region family: {family}")
        if not requirements and family != "run-guidance":
            raise AgreementContractError(f"requirement-region family is empty: {family}")
        body = {
            "family": family,
            "requirements": [
                {"requirement_id": requirement_id, "text": body}
                for requirement_id, body in requirements
            ],
        }
        regions.append(RequirementRegion(family, _sha256(_canonical(body)), requirements))
    ids = [item[0] for region in regions for item in region.requirements]
    if len(ids) != len(set(ids)):
        raise AgreementContractError("requirement ids collide across generated-region families")
    return tuple(regions)


def _evidence_ref(root: pathlib.Path, value: object, label: str) -> tuple[pathlib.Path, str]:
    if not isinstance(value, dict):
        raise AgreementContractError(f"{label} must be an evidence reference")
    _exact_keys(value, {"path", "digest"}, label)
    path = _closed_relative(root, value["path"], f"{label} path")
    expected = _digest(value["digest"], f"{label} digest")
    actual = _sha256(_read_regular(path, ceiling=MAX_ARTIFACT_BYTES, allow_empty=True))
    if actual != expected:
        raise AgreementContractError(f"{label} digest does not match retained bytes")
    return path, expected


def _validate_inventory(
    root: pathlib.Path,
    value: Mapping[str, Any],
    *,
    run_id: str,
    regions: Sequence[RequirementRegion],
) -> dict[str, Mapping[str, Any]]:
    _exact_keys(value, {"schema_version", "run_id", "requirement_regions"}, "participant inventory")
    if value.get("schema_version") != INVENTORY_SCHEMA or value.get("run_id") != run_id:
        raise AgreementContractError("participant inventory schema or run differs")
    rows = value.get("requirement_regions")
    if not isinstance(rows, list) or len(rows) != len(regions):
        raise AgreementContractError("participant inventory region membership differs")
    expected_region_keys = {"family", "region_digest", "requirements"}
    expected_requirement_keys = {
        "requirement_id",
        "criticality",
        "derivation",
        "derivation_evidence",
        "basis",
        "limitations",
        "participants",
    }
    indexed: dict[str, Mapping[str, Any]] = {}
    for row, expected_region in zip(rows, regions, strict=True):
        if not isinstance(row, dict):
            raise AgreementContractError("participant inventory region must be an object")
        _exact_keys(row, expected_region_keys, "participant inventory region")
        if (
            row["family"] != expected_region.family
            or row["region_digest"] != expected_region.region_digest
        ):
            raise AgreementContractError("participant inventory region is stale or reordered")
        requirement_rows = row["requirements"]
        if not isinstance(requirement_rows, list):
            raise AgreementContractError("participant inventory requirements must be a list")
        expected_ids = [item[0] for item in expected_region.requirements]
        actual_ids: list[str] = []
        for item in requirement_rows:
            if not isinstance(item, dict):
                raise AgreementContractError("participant inventory requirement must be an object")
            _exact_keys(item, expected_requirement_keys, "participant inventory requirement")
            requirement_id = _identifier(item["requirement_id"], "inventory requirement id")
            actual_ids.append(requirement_id)
            if item["criticality"] not in {"critical", "standard", "cosmetic"}:
                raise AgreementContractError(f"{requirement_id} has invalid criticality")
            derivation = item["derivation"]
            if derivation not in {"mechanical", "bounded-manual"}:
                raise AgreementContractError(f"{requirement_id} has invalid inventory derivation")
            _path, evidence_digest = _evidence_ref(
                root, item["derivation_evidence"], f"{requirement_id} derivation evidence"
            )
            basis = _text(item["basis"], f"{requirement_id} inventory basis")
            if evidence_digest not in basis:
                raise AgreementContractError(
                    f"{requirement_id} inventory basis must cite its evidence digest"
                )
            limitations = item["limitations"]
            if not isinstance(limitations, list) or any(
                not isinstance(entry, str) or not entry.strip() for entry in limitations
            ):
                raise AgreementContractError(f"{requirement_id} limitations must be a text list")
            if derivation == "mechanical" and limitations:
                raise AgreementContractError(
                    f"{requirement_id} mechanical inventory cannot retain manual limitations"
                )
            if derivation == "bounded-manual" and (
                not limitations or item["criticality"] == "critical"
            ):
                raise AgreementContractError(
                    f"{requirement_id} bounded-manual inventory needs limitations "
                    "and cannot clear Critical"
                )
            participants = item["participants"]
            if (
                not isinstance(participants, list)
                or not 1 <= len(participants) <= 64
                or any(
                    not isinstance(entry, str) or not _ID.fullmatch(entry) for entry in participants
                )
                or participants != sorted(set(participants))
            ):
                raise AgreementContractError(
                    f"{requirement_id} participants must be a sorted, non-empty, "
                    "unique identifier list"
                )
            indexed[requirement_id] = item
        if actual_ids != expected_ids:
            raise AgreementContractError(
                f"participant inventory membership differs for region {expected_region.family}"
            )
    return indexed


def _nullable_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _validate_axes(value: object, requirement_id: str) -> None:
    if not isinstance(value, list) or len(value) != len(AXES):
        raise AgreementContractError(f"{requirement_id} must disposition every agreement axis")
    actual: list[str] = []
    for row in value:
        if not isinstance(row, dict):
            raise AgreementContractError(f"{requirement_id} agreement axis must be an object")
        _exact_keys(row, {"axis", "disposition", "basis", "plan"}, "agreement axis")
        axis = row["axis"]
        actual.append(axis)
        if row["disposition"] not in {"applicable", "not-applicable"}:
            raise AgreementContractError(f"{requirement_id}/{axis} has invalid disposition")
        _text(row["basis"], f"{requirement_id}/{axis} basis")
        if row["disposition"] == "applicable":
            _text(row["plan"], f"{requirement_id}/{axis} plan")
        elif row["plan"] is not None:
            raise AgreementContractError(
                f"{requirement_id}/{axis} not-applicable disposition cannot retain a plan"
            )
    if tuple(actual) != AXES:
        raise AgreementContractError(f"{requirement_id} agreement axes are missing or reordered")


def _validate_contract(
    value: Mapping[str, Any],
    *,
    run_id: str,
    inventory_digest: str,
    inventory_items: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    _exact_keys(
        value,
        {"schema_version", "run_id", "participant_inventory_digest", "entries"},
        "agreement contract",
    )
    if value.get("schema_version") != CONTRACT_SCHEMA or value.get("run_id") != run_id:
        raise AgreementContractError("agreement contract schema or run differs")
    if value.get("participant_inventory_digest") != inventory_digest:
        raise AgreementContractError("agreement contract carries a stale participant inventory")
    rows = value.get("entries")
    if not isinstance(rows, list):
        raise AgreementContractError("agreement contract entries must be a list")
    expected_keys = {
        "requirement_id",
        "single_path_basis",
        "shared_authority",
        "semantic_residue",
        "agreement_oracle",
        "producer_mismatch",
        "consumer_mismatch",
        "axes",
    }
    entries: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AgreementContractError("agreement contract entry must be an object")
        _exact_keys(row, expected_keys, "agreement contract entry")
        requirement_id = _identifier(row["requirement_id"], "agreement requirement id")
        inventory = inventory_items.get(requirement_id)
        if inventory is None or requirement_id in entries:
            raise AgreementContractError(
                f"agreement contract has unknown or duplicate {requirement_id}"
            )
        participant_count = len(inventory["participants"])
        if participant_count == 1:
            basis = _text(row["single_path_basis"], f"{requirement_id} single-path basis")
            evidence_digest = inventory["derivation_evidence"]["digest"]
            if evidence_digest not in basis:
                raise AgreementContractError(
                    f"{requirement_id} single-path basis must cite the inventory evidence digest"
                )
            for field in (
                "shared_authority",
                "semantic_residue",
                "agreement_oracle",
                "producer_mismatch",
                "consumer_mismatch",
            ):
                if row[field] is not None:
                    raise AgreementContractError(
                        f"{requirement_id} single-path entry cannot carry cross-path field {field}"
                    )
            if row["axes"] != []:
                raise AgreementContractError(
                    f"{requirement_id} single-path entry cannot carry cross-path axes"
                )
        else:
            if row["single_path_basis"] is not None:
                raise AgreementContractError(
                    f"{requirement_id} has multiple participants and cannot be downgraded"
                )
            for field in (
                "shared_authority",
                "semantic_residue",
                "agreement_oracle",
                "producer_mismatch",
                "consumer_mismatch",
            ):
                _nullable_text(row[field], f"{requirement_id} {field}")
                if row[field] is None:
                    raise AgreementContractError(f"{requirement_id} is missing {field}")
            if row["producer_mismatch"] == row["consumer_mismatch"]:
                raise AgreementContractError(
                    f"{requirement_id} must plan distinct producer and consumer mismatches"
                )
            _validate_axes(row["axes"], requirement_id)
        entries[requirement_id] = row
    if list(entries) != list(inventory_items):
        raise AgreementContractError(
            "agreement contract membership or order differs from inventory"
        )
    return entries


def load_plan(root: pathlib.Path, artifacts: pathlib.Path) -> AgreementPlan | None:
    required = required_configuration(root)
    if required is None:
        return None
    _version, families = required
    harness = _read_harness(root)
    if harness is None:
        raise AgreementContractError("harness metadata disappeared while loading agreement plan")
    run_id = _identifier(harness.get("run_id"), "harness run id")
    regions = derive_regions(artifacts / "product-specification.md", families)
    inventory, inventory_raw = _json_file(artifacts / "agreement" / "participant-inventory.json")
    inventory_items = _validate_inventory(
        root,
        inventory,
        run_id=run_id,
        regions=regions,
    )
    contract, contract_raw = _json_file(artifacts / "agreement" / "contract.json")
    inventory_digest = _sha256(inventory_raw)
    contract_entries = _validate_contract(
        contract,
        run_id=run_id,
        inventory_digest=inventory_digest,
        inventory_items=inventory_items,
    )
    return AgreementPlan(
        run_id=run_id,
        families=families,
        regions=regions,
        inventory=inventory,
        inventory_digest=inventory_digest,
        contract=contract,
        contract_digest=_sha256(contract_raw),
        inventory_items=inventory_items,
        contract_entries=contract_entries,
    )


def render_section(plan: AgreementPlan) -> str:
    lines = [
        BEGIN,
        "## Cross-path agreement register (generated; do not hand-edit)",
        "",
        f"Protocol: `{CONTRACT_SCHEMA}`",
        f"Participant inventory: `{plan.inventory_digest}`",
        f"Agreement contract: `{plan.contract_digest}`",
        "Requirement regions: "
        + ", ".join(f"`{region.family}@{region.region_digest}`" for region in plan.regions),
        "",
    ]
    for requirement_id, inventory in plan.inventory_items.items():
        entry = plan.contract_entries[requirement_id]
        participants = list(inventory["participants"])
        classification = "cross-path" if len(participants) >= 2 else "single-path"
        closure = "refused" if inventory["derivation"] == "mechanical" else "disclosed"
        lines.extend(
            [
                f"- **{requirement_id}** — `{classification}`; inventory-closure=`{closure}`",
                "  - Participants: " + ", ".join(f"`{item}`" for item in participants),
                f"  - Inventory: `{inventory['derivation']}`; "
                f"evidence=`{inventory['derivation_evidence']['digest']}`",
                f"  - Basis: {json.dumps(inventory['basis'], ensure_ascii=False)}",
            ]
        )
        if inventory["limitations"]:
            lines.append(
                "  - Disclosed limitations: "
                + json.dumps(inventory["limitations"], ensure_ascii=False)
            )
        if classification == "single-path":
            basis = json.dumps(entry["single_path_basis"], ensure_ascii=False)
            lines.append(f"  - Single-path basis: {basis}")
            continue
        shared_authority = json.dumps(entry["shared_authority"], ensure_ascii=False)
        semantic_residue = json.dumps(entry["semantic_residue"], ensure_ascii=False)
        agreement_oracle = json.dumps(entry["agreement_oracle"], ensure_ascii=False)
        producer_mismatch = json.dumps(entry["producer_mismatch"], ensure_ascii=False)
        consumer_mismatch = json.dumps(entry["consumer_mismatch"], ensure_ascii=False)
        lines.extend(
            [
                f"  - Shared authority: {shared_authority}",
                f"  - Semantic residue: {semantic_residue}",
                f"  - Agreement oracle: {agreement_oracle}",
                f"  - Producer mismatch witness: {producer_mismatch}",
                f"  - Consumer mismatch witness: {consumer_mismatch}",
            ]
        )
        for axis in entry["axes"]:
            suffix = (
                f"; plan={json.dumps(axis['plan'], ensure_ascii=False)}"
                if axis["plan"] is not None
                else ""
            )
            lines.append(
                f"  - {axis['axis']}: `{axis['disposition']}`; "
                f"basis={json.dumps(axis['basis'], ensure_ascii=False)}{suffix}"
            )
    lines.extend(["", END, ""])
    return "\n".join(lines)


def _strategy_section(text: str) -> str:
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise AgreementContractError(
            "Testing Strategy must contain exactly one generated agreement register"
        )
    start = text.index(BEGIN)
    finish = text.index(END, start) + len(END)
    if finish < len(text) and text[finish] == "\n":
        finish += 1
    return text[start:finish]


def _replace_strategy(text: str, section: str) -> str:
    counts = text.count(BEGIN), text.count(END)
    if counts == (0, 0):
        return text.rstrip("\n") + "\n\n" + section
    if counts != (1, 1):
        raise AgreementContractError("Testing Strategy agreement markers are malformed")
    start = text.index(BEGIN)
    finish = text.index(END, start) + len(END)
    if finish < len(text) and text[finish] == "\n":
        finish += 1
    return text[:start] + section + text[finish:]


def _atomic_replace(path: pathlib.Path, old_raw: bytes, new_raw: bytes) -> None:
    temporary = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    try:
        temporary.write(new_raw)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        if _read_regular(path, ceiling=MAX_ARTIFACT_BYTES) != old_raw:
            raise AgreementContractError(f"artifact changed before replacement: {path}")
        os.chmod(temporary.name, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary.name, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.close()
        try:
            os.unlink(temporary.name)
        except FileNotFoundError:
            pass


def update_strategy(root: pathlib.Path, artifacts: pathlib.Path) -> AgreementPlan | None:
    plan = load_plan(root, artifacts)
    if plan is None:
        return None
    strategy = artifacts / "testing-strategy.md"
    if strategy.with_name(strategy.name + ".digest").exists():
        raise AgreementContractError(
            "refusing to rewrite a ratified Testing Strategy; supersede it before signing"
        )
    raw = _read_regular(strategy, ceiling=MAX_ARTIFACT_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgreementContractError("Testing Strategy is not UTF-8") from exc
    rendered = _replace_strategy(text, render_section(plan)).encode("utf-8")
    _atomic_replace(strategy, raw, rendered)
    return plan


def verify_plan(root: pathlib.Path, artifacts: pathlib.Path) -> AgreementPlan | None:
    plan = load_plan(root, artifacts)
    if plan is None:
        return None
    try:
        strategy = _read_regular(
            artifacts / "testing-strategy.md", ceiling=MAX_ARTIFACT_BYTES
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgreementContractError("Testing Strategy is not UTF-8") from exc
    if _strategy_section(strategy) != render_section(plan):
        raise AgreementContractError(
            "Testing Strategy agreement register differs from fresh derivation"
        )
    return plan


def _validate_witness(
    root: pathlib.Path,
    reference: object,
    *,
    run_id: str,
    requirement_id: str,
    direction: str,
    candidate_sha: str,
    local_suite_digest: str,
    agreement_oracle_digest: str,
) -> Mapping[str, Any]:
    path, _address = _evidence_ref(root, reference, f"{requirement_id}/{direction} witness")
    witness, _raw = _json_file(path)
    expected = {
        "schema_version",
        "run_id",
        "requirement_id",
        "direction",
        "candidate_sha",
        "local_suite_digest",
        "agreement_oracle_digest",
        "patch_digest",
        "local_command_digest",
        "agreement_command_digest",
        "baseline_local_exit",
        "baseline_agreement_exit",
        "mutated_local_exit",
        "mutated_agreement_exit",
        "witnessed",
    }
    _exact_keys(witness, expected, f"{requirement_id}/{direction} witness")
    exact = {
        "schema_version": WITNESS_SCHEMA,
        "run_id": run_id,
        "requirement_id": requirement_id,
        "direction": direction,
        "candidate_sha": candidate_sha,
        "local_suite_digest": local_suite_digest,
        "agreement_oracle_digest": agreement_oracle_digest,
        "baseline_local_exit": 0,
        "baseline_agreement_exit": 0,
        "mutated_local_exit": 0,
        "witnessed": True,
    }
    if any(witness.get(key) != value for key, value in exact.items()):
        raise AgreementContractError(
            f"{requirement_id}/{direction} does not prove local-green/agreement-red"
        )
    if (
        not isinstance(witness.get("mutated_agreement_exit"), int)
        or witness["mutated_agreement_exit"] == 0
    ):
        raise AgreementContractError(
            f"{requirement_id}/{direction} agreement oracle did not turn red"
        )
    for field in (
        "patch_digest",
        "local_command_digest",
        "agreement_command_digest",
    ):
        _digest(witness.get(field), f"{requirement_id}/{direction} {field}")
    return witness


def _validate_structural_review(
    root: pathlib.Path,
    reference: object,
    *,
    run_id: str,
    requirement_id: str,
    candidate_sha: str,
) -> None:
    path, _address = _evidence_ref(root, reference, f"{requirement_id} structural review")
    review, _raw = _json_file(path)
    _exact_keys(
        review,
        {
            "schema_version",
            "run_id",
            "requirement_id",
            "candidate_sha",
            "reviewer",
            "reviewer_family",
            "structural_authority_digest",
            "residue_fully_carried",
            "basis",
        },
        f"{requirement_id} structural review",
    )
    if (
        review.get("schema_version") != STRUCTURAL_REVIEW_SCHEMA
        or review.get("run_id") != run_id
        or review.get("requirement_id") != requirement_id
        or review.get("candidate_sha") != candidate_sha
        or review.get("residue_fully_carried") is not True
    ):
        raise AgreementContractError(f"{requirement_id} structural review has the wrong subject")
    _text(review.get("reviewer"), f"{requirement_id} reviewer")
    _text(review.get("reviewer_family"), f"{requirement_id} reviewer family")
    _digest(review.get("structural_authority_digest"), f"{requirement_id} authority digest")
    _text(review.get("basis"), f"{requirement_id} structural review basis")


def verify_evidence(
    root: pathlib.Path,
    artifacts: pathlib.Path,
    *,
    candidate_sha: str,
) -> dict[str, Any] | None:
    plan = verify_plan(root, artifacts)
    if plan is None:
        return None
    if not _COMMIT.fullmatch(candidate_sha):
        raise AgreementContractError("candidate SHA is not an exact commit id")
    evidence, _raw = _json_file(artifacts / "agreement" / "evidence.json")
    _exact_keys(
        evidence,
        {"schema_version", "run_id", "agreement_contract_digest", "candidate_sha", "results"},
        "agreement evidence",
    )
    if (
        evidence.get("schema_version") != EVIDENCE_SCHEMA
        or evidence.get("run_id") != plan.run_id
        or evidence.get("agreement_contract_digest") != plan.contract_digest
        or evidence.get("candidate_sha") != candidate_sha
    ):
        raise AgreementContractError("agreement evidence is stale or belongs to another subject")
    rows = evidence.get("results")
    if not isinstance(rows, list):
        raise AgreementContractError("agreement evidence results must be a list")
    expected_ids = [
        requirement_id
        for requirement_id, item in plan.inventory_items.items()
        if len(item["participants"]) >= 2
    ]
    actual_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise AgreementContractError("agreement evidence result must be an object")
        _exact_keys(
            row,
            {
                "requirement_id",
                "disposition",
                "closure_class",
                "local_suite_digest",
                "agreement_oracle_digest",
                "producer_witness",
                "consumer_witness",
                "independent_review",
            },
            "agreement evidence result",
        )
        requirement_id = _identifier(row["requirement_id"], "agreement evidence requirement")
        actual_ids.append(requirement_id)
        disposition = row["disposition"]
        if disposition == "witnessed":
            if row["closure_class"] != "refused":
                raise AgreementContractError(
                    f"{requirement_id} witnessed evidence must have refused closure"
                )
            local_suite_digest = _digest(
                row["local_suite_digest"], f"{requirement_id} local suite digest"
            )
            oracle_digest = _digest(
                row["agreement_oracle_digest"], f"{requirement_id} agreement oracle digest"
            )
            if row["independent_review"] is not None:
                raise AgreementContractError(
                    f"{requirement_id} witnessed result cannot substitute a structural review"
                )
            witnesses = {}
            for direction in ("producer", "consumer"):
                witnesses[direction] = _validate_witness(
                    root,
                    row[f"{direction}_witness"],
                    run_id=plan.run_id,
                    requirement_id=requirement_id,
                    direction=direction,
                    candidate_sha=candidate_sha,
                    local_suite_digest=local_suite_digest,
                    agreement_oracle_digest=oracle_digest,
                )
            if witnesses["producer"]["patch_digest"] == witnesses["consumer"]["patch_digest"]:
                raise AgreementContractError(
                    f"{requirement_id} producer and consumer evidence use the same mutation"
                )
        elif disposition == "structurally-carried":
            if row["closure_class"] != "routed":
                raise AgreementContractError(
                    f"{requirement_id} structural evidence must have routed closure"
                )
            if any(
                row[field] is not None
                for field in (
                    "local_suite_digest",
                    "agreement_oracle_digest",
                    "producer_witness",
                    "consumer_witness",
                )
            ):
                raise AgreementContractError(
                    f"{requirement_id} structural disposition cannot carry mismatch witnesses"
                )
            _validate_structural_review(
                root,
                row["independent_review"],
                run_id=plan.run_id,
                requirement_id=requirement_id,
                candidate_sha=candidate_sha,
            )
        else:
            raise AgreementContractError(f"{requirement_id} has invalid evidence disposition")
    if actual_ids != expected_ids:
        raise AgreementContractError("agreement evidence membership or order differs from contract")
    return evidence


def _summary(plan: AgreementPlan | None) -> dict[str, Any]:
    if plan is None:
        return {"required": False, "schema_version": CONTRACT_SCHEMA}
    cross = sum(len(item["participants"]) >= 2 for item in plan.inventory_items.values())
    manual = sum(item["derivation"] == "bounded-manual" for item in plan.inventory_items.values())
    return {
        "required": True,
        "schema_version": CONTRACT_SCHEMA,
        "run_id": plan.run_id,
        "families": list(plan.families),
        "requirement_count": len(plan.inventory_items),
        "cross_path_count": cross,
        "single_path_count": len(plan.inventory_items) - cross,
        "bounded_manual_count": manual,
        "participant_inventory_digest": plan.inventory_digest,
        "agreement_contract_digest": plan.contract_digest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("inspect", "render", "update-strategy", "verify-plan", "verify-evidence"),
    )
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--artifacts", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-sha")
    arguments = parser.parse_args()
    try:
        if arguments.command == "update-strategy":
            plan = update_strategy(arguments.root, arguments.artifacts)
            print(json.dumps(_summary(plan), sort_keys=True))
        elif arguments.command == "verify-plan":
            plan = verify_plan(arguments.root, arguments.artifacts)
            print(json.dumps(_summary(plan), sort_keys=True))
        elif arguments.command == "verify-evidence":
            if not arguments.candidate_sha:
                raise AgreementContractError("verify-evidence requires --candidate-sha")
            evidence = verify_evidence(
                arguments.root,
                arguments.artifacts,
                candidate_sha=arguments.candidate_sha,
            )
            print(
                json.dumps(
                    {
                        "required": evidence is not None,
                        "verified": evidence is not None,
                        "schema_version": EVIDENCE_SCHEMA,
                    },
                    sort_keys=True,
                )
            )
        else:
            plan = load_plan(arguments.root, arguments.artifacts)
            if arguments.command == "render":
                if plan is not None:
                    sys.stdout.write(render_section(plan))
            else:
                print(json.dumps(_summary(plan), sort_keys=True))
    except AgreementContractError as exc:
        print(f"agreement contract refused: {exc}", file=sys.stderr)
        return 71
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
