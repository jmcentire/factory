#!/usr/bin/env python3
"""Admit, compile, project, and verify per-run standards, loops, and recipes.

Selected source documents remain externally checkpoint-bound configuration.  Only obligations
that the Validator applies and the human ratifies through generated Product, Architecture, and
Testing regions become intent.  Host checks prove exact membership, routing, and evidence
identity; they do not claim that a digest understands a standard or that routing proves
substantive compliance.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

from agreement_contract import (  # noqa: E402
    AgreementContractError,
    _atomic_replace,
    _canonical,
    _closed_relative,
    _digest,
    _evidence_ref,
    _exact_keys,
    _identifier,
    _json_file,
    _read_harness,
    _read_regular,
    _sha256,
    _text,
)

CONTRACT_SCHEMA = "factory-run-guidance/1"
SELECTION_SCHEMA = "factory-run-guidance-selection/1"
ADMISSION_SCHEMA = "factory-run-guidance-admission/1"
APPLICATION_SCHEMA = "factory-run-guidance-application/1"
CLASSIFICATION_REVIEW_SCHEMA = "factory-run-guidance-classification-review/1"
PROJECTION_SCHEMA = "factory-run-guidance-projection/1"
EVIDENCE_SCHEMA = "factory-run-guidance-evidence/1"
OBSERVATION_SCHEMA = "factory-run-guidance-observation/1"
FINDING_RESOLUTION_SCHEMA = "factory-run-guidance-finding-resolution/1"

RESERVED_SOURCE = "factory-run-guidance"
KINDS = ("recipe", "standard", "loop")
SUBJECT_CLASSES = ("behavioral", "procedural", "constructional")
ROLES = ("validator", "orchestrator", "coder", "tester")
ROUTES = {
    "behavioral": "acceptance-obligation",
    "procedural": "process-checkpoint",
    "constructional": "architecture-conformance",
}
TARGETS = {
    "behavioral": ["product-specification", "testing-strategy"],
    "procedural": ["architecture", "testing-strategy"],
    "constructional": ["architecture", "testing-strategy"],
}
MARKERS = {
    "product-specification.md": (
        "<!-- FACTORY-RUN-GUIDANCE:BEGIN -->",
        "<!-- FACTORY-RUN-GUIDANCE:END -->",
    ),
    "architecture.md": (
        "<!-- FACTORY-RUN-GUIDANCE-ARCHITECTURE:BEGIN -->",
        "<!-- FACTORY-RUN-GUIDANCE-ARCHITECTURE:END -->",
    ),
    "testing-strategy.md": (
        "<!-- FACTORY-RUN-GUIDANCE-TESTING:BEGIN -->",
        "<!-- FACTORY-RUN-GUIDANCE-TESTING:END -->",
    ),
}
MAX_GUIDANCE_BYTES = 64 * 1024 * 1024
_GUIDANCE_ID = re.compile(r"^G-[A-Za-z0-9][A-Za-z0-9._-]{0,123}$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class RunGuidanceError(RuntimeError):
    """Selected guidance is unsafe, stale, incomplete, or falsely characterized."""


@dataclass(frozen=True)
class GuidanceSelection:
    root: pathlib.Path
    run_id: str
    generation: int
    selection: Mapping[str, Any]
    selection_digest: str
    obligations: Mapping[str, Mapping[str, Any]]
    documents: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class GuidancePlan:
    selected: GuidanceSelection
    application: Mapping[str, Any]
    application_digest: str
    application_rows: Mapping[str, Mapping[str, Any]]


def _guidance_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _GUIDANCE_ID.fullmatch(value):
        raise RunGuidanceError(f"{label} is not a stable G-* obligation id")
    return value


def _install_exact(path: pathlib.Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
    except FileExistsError as exc:
        if _read_regular(path, ceiling=MAX_GUIDANCE_BYTES, allow_empty=True) != raw:
            raise RunGuidanceError(f"retained guidance path has conflicting bytes: {path}") from exc
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _parse_config_sources(entries: Sequence[str]) -> dict[str, pathlib.Path]:
    result: dict[str, pathlib.Path] = {}
    for entry in entries:
        name, separator, raw_path = entry.partition("=")
        if not separator or not name or name in result or not _identifier(name, "config source"):
            raise RunGuidanceError("configuration sources are malformed or duplicated")
        path = pathlib.Path(raw_path)
        if not path.is_absolute():
            raise RunGuidanceError(f"configuration source is not absolute: {name}")
        result[name] = path
    return result


def _parse_config_digests(entries: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        name, separator, address = entry.partition("=")
        if not separator or not name or name in result or not _identifier(name, "config digest"):
            raise RunGuidanceError("configuration digests are malformed or duplicated")
        result[name] = _digest(address, f"{name} configuration digest")
    return result


def _selection_from_raw(
    raw: bytes,
    *,
    run_id: str,
    generation: int,
) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunGuidanceError(f"guidance selector is not JSON: {exc}") from exc
    if not isinstance(value, dict) or raw != _canonical(value):
        raise RunGuidanceError("guidance selector must be a canonical JSON object")
    _exact_keys(
        value,
        {"schema_version", "run_id", "generation", "documents"},
        "guidance selection",
    )
    if (
        value.get("schema_version") != SELECTION_SCHEMA
        or value.get("run_id") != run_id
        or value.get("generation") != generation
    ):
        raise RunGuidanceError("guidance selection schema or run generation differs")
    documents = value.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RunGuidanceError("guidance selection documents must be a non-empty list")
    document_index: dict[str, Mapping[str, Any]] = {}
    obligation_index: dict[str, Mapping[str, Any]] = {}
    expected_document_keys = {"source_name", "source_digest", "kind", "obligations"}
    expected_obligation_keys = {
        "obligation_id",
        "text",
        "subject_class",
        "classification_basis",
        "roles",
        "authority_targets",
    }
    for document in documents:
        if not isinstance(document, dict):
            raise RunGuidanceError("guidance document entry must be an object")
        _exact_keys(document, expected_document_keys, "guidance document entry")
        source_name = _identifier(document["source_name"], "guidance source name")
        if source_name == RESERVED_SOURCE or source_name in document_index:
            raise RunGuidanceError("guidance source names are duplicated or reserved")
        _digest(document["source_digest"], f"{source_name} digest")
        if document["kind"] not in KINDS:
            raise RunGuidanceError(f"{source_name} has an invalid guidance kind")
        obligations = document["obligations"]
        if not isinstance(obligations, list) or not obligations:
            raise RunGuidanceError(f"{source_name} must enumerate at least one obligation")
        local_ids: list[str] = []
        for obligation in obligations:
            if not isinstance(obligation, dict):
                raise RunGuidanceError("guidance obligation must be an object")
            _exact_keys(obligation, expected_obligation_keys, "guidance obligation")
            obligation_id = _guidance_id(obligation["obligation_id"], "guidance obligation id")
            if obligation_id in obligation_index:
                raise RunGuidanceError(f"duplicate guidance obligation: {obligation_id}")
            _text(obligation["text"], f"{obligation_id} text", maximum=16_384)
            subject = obligation["subject_class"]
            if subject not in SUBJECT_CLASSES:
                raise RunGuidanceError(f"{obligation_id} has an invalid subject class")
            _text(obligation["classification_basis"], f"{obligation_id} classification basis")
            roles = obligation["roles"]
            if (
                not isinstance(roles, list)
                or not roles
                or roles != [role for role in ROLES if role in roles]
                or len(roles) != len(set(roles))
            ):
                raise RunGuidanceError(f"{obligation_id} roles are not canonical")
            if subject != "behavioral" and "tester" in roles:
                raise RunGuidanceError(
                    f"{obligation_id} cannot route procedural or constructional guidance "
                    "to the independent Tester"
                )
            if obligation["authority_targets"] != TARGETS[subject]:
                raise RunGuidanceError(
                    f"{obligation_id} authority targets differ from its subject-class route"
                )
            local_ids.append(obligation_id)
            obligation_index[obligation_id] = {**obligation, "source_name": source_name}
        if local_ids != sorted(local_ids):
            raise RunGuidanceError(f"{source_name} obligations are not sorted")
        document_index[source_name] = document
    if list(document_index) != sorted(document_index):
        raise RunGuidanceError("guidance documents are not sorted by source name")
    return value, document_index, obligation_index


def admit(
    root: pathlib.Path,
    *,
    run_id: str,
    generation: int,
    config_sources: Sequence[str],
    config_digests: Sequence[str],
) -> dict[str, Any]:
    sources = _parse_config_sources(config_sources)
    expected_digests = _parse_config_digests(config_digests)
    if set(sources) != set(expected_digests):
        raise RunGuidanceError("verified configuration source and digest memberships differ")
    selector_path = sources.get(RESERVED_SOURCE)
    if selector_path is None:
        return {
            "required": False,
            "schema_version": CONTRACT_SCHEMA,
            "state": "none",
            "selection_digest": None,
            "source_digests": {},
        }
    selector_raw = _read_regular(selector_path, ceiling=MAX_GUIDANCE_BYTES)
    if _sha256(selector_raw) != expected_digests[RESERVED_SOURCE]:
        raise RunGuidanceError("guidance selector changed after resume verification")
    selection, documents, _obligations = _selection_from_raw(
        selector_raw,
        run_id=run_id,
        generation=generation,
    )
    retained_sources: list[dict[str, str]] = []
    source_digests: dict[str, str] = {}
    for source_name, document in documents.items():
        source_path = sources.get(source_name)
        if source_path is None:
            raise RunGuidanceError(
                f"selected guidance source is absent from resume config: {source_name}"
            )
        raw = _read_regular(source_path, ceiling=MAX_GUIDANCE_BYTES)
        observed = _sha256(raw)
        if observed != expected_digests[source_name]:
            raise RunGuidanceError(
                f"selected guidance source changed after resume verification: {source_name}"
            )
        if observed != document["source_digest"]:
            raise RunGuidanceError(f"selected guidance source digest differs: {source_name}")
        retained = (
            root
            / "guidance"
            / "sources"
            / f"{source_name}--{observed.removeprefix('sha256:')}.source"
        )
        _install_exact(retained, raw)
        retained_sources.append(
            {
                "source_name": source_name,
                "source_digest": observed,
                "retained_path": retained.relative_to(root).as_posix(),
            }
        )
        source_digests[source_name] = observed
    selection_digest = _sha256(selector_raw)
    _install_exact(root / "guidance" / "selection.json", selector_raw)
    admission = {
        "schema_version": ADMISSION_SCHEMA,
        "run_id": run_id,
        "generation": generation,
        "selection_digest": selection_digest,
        "sources": retained_sources,
    }
    _install_exact(root / "guidance" / "admission.json", _canonical(admission))
    return {
        "required": True,
        "schema_version": CONTRACT_SCHEMA,
        "state": "pending-application",
        "selection_digest": selection_digest,
        "source_digests": source_digests,
    }


def load_selection(root: pathlib.Path) -> GuidanceSelection | None:
    harness = _read_harness(root)
    if harness is None or "guidance_contract_version" not in harness:
        return None
    if harness.get("guidance_contract_version") != CONTRACT_SCHEMA:
        raise RunGuidanceError("unsupported run-guidance contract version")
    run_id = _identifier(harness.get("run_id"), "harness run id")
    generation = harness.get("guidance_generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise RunGuidanceError("harness guidance generation is invalid")
    expected_digest = harness.get("guidance_selection_digest")
    expected_sources = harness.get("guidance_source_digests")
    if expected_digest is None:
        if harness.get("guidance_state") != "none" or expected_sources != {}:
            raise RunGuidanceError("none guidance metadata carries selected state")
        return None
    _digest(expected_digest, "harness guidance selection digest")
    if not isinstance(expected_sources, dict):
        raise RunGuidanceError("harness guidance source digests are invalid")
    selection, selection_raw = _json_file(root / "guidance" / "selection.json")
    if _sha256(selection_raw) != expected_digest:
        raise RunGuidanceError("retained guidance selection differs from ignition metadata")
    checked, documents, obligations = _selection_from_raw(
        selection_raw,
        run_id=run_id,
        generation=generation,
    )
    admission, _admission_raw = _json_file(root / "guidance" / "admission.json")
    _exact_keys(
        admission,
        {
            "schema_version",
            "run_id",
            "generation",
            "selection_digest",
            "sources",
        },
        "guidance admission",
    )
    if (
        admission.get("schema_version") != ADMISSION_SCHEMA
        or admission.get("run_id") != run_id
        or admission.get("generation") != generation
        or admission.get("selection_digest") != expected_digest
    ):
        raise RunGuidanceError("guidance admission belongs to another selection")
    rows = admission.get("sources")
    if not isinstance(rows, list) or len(rows) != len(documents):
        raise RunGuidanceError("guidance admission source membership differs")
    actual_sources: dict[str, str] = {}
    for row, (source_name, document) in zip(rows, documents.items(), strict=True):
        if not isinstance(row, dict):
            raise RunGuidanceError("guidance admission source is not an object")
        _exact_keys(row, {"source_name", "source_digest", "retained_path"}, "admitted source")
        if row["source_name"] != source_name or row["source_digest"] != document["source_digest"]:
            raise RunGuidanceError("guidance admission source is stale or reordered")
        retained = _closed_relative(root, row["retained_path"], f"{source_name} retained source")
        if (
            _sha256(_read_regular(retained, ceiling=MAX_GUIDANCE_BYTES))
            != row["source_digest"]
        ):
            raise RunGuidanceError(f"retained guidance source changed: {source_name}")
        actual_sources[source_name] = row["source_digest"]
    if actual_sources != expected_sources:
        raise RunGuidanceError("guidance source digest map differs from ignition metadata")
    return GuidanceSelection(
        root=root,
        run_id=run_id,
        generation=generation,
        selection=checked,
        selection_digest=expected_digest,
        obligations=obligations,
        documents=documents,
    )


def _id_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or value != sorted(set(value))
        or any(not isinstance(item, str) or not _identifier(item, label) for item in value)
    ):
        raise RunGuidanceError(f"{label} must be a sorted unique identifier list")
    return value


def _review(
    selected: GuidanceSelection,
    reference: object,
    *,
    obligation_id: str,
    application_subject_digest: str,
) -> None:
    path, _digest_value = _evidence_ref(selected.root, reference, f"{obligation_id} review")
    review, _raw = _json_file(path)
    _exact_keys(
        review,
        {
            "schema_version",
            "run_id",
            "generation",
            "selection_digest",
            "obligation_id",
            "application_subject_digest",
            "reviewer",
            "reviewer_family",
            "independent_of_validator",
            "classification_upheld",
            "application_upheld",
            "basis",
        },
        f"{obligation_id} review",
    )
    if (
        review.get("schema_version") != CLASSIFICATION_REVIEW_SCHEMA
        or review.get("run_id") != selected.run_id
        or review.get("generation") != selected.generation
        or review.get("selection_digest") != selected.selection_digest
        or review.get("obligation_id") != obligation_id
        or review.get("application_subject_digest") != application_subject_digest
        or review.get("independent_of_validator") is not True
        or review.get("classification_upheld") is not True
        or review.get("application_upheld") is not True
    ):
        raise RunGuidanceError(f"{obligation_id} independent review did not uphold the routing")
    reviewer = _text(review.get("reviewer"), f"{obligation_id} reviewer")
    _text(review.get("reviewer_family"), f"{obligation_id} reviewer family")
    _text(review.get("basis"), f"{obligation_id} review basis")
    harness = _read_harness(selected.root) or {}
    if reviewer.casefold() == str(harness.get("validator_agent", "")).casefold():
        raise RunGuidanceError(f"{obligation_id} review is not independent of the Validator")


def load_plan(root: pathlib.Path, artifacts: pathlib.Path) -> GuidancePlan | None:
    selected = load_selection(root)
    if selected is None:
        return None
    application, raw = _json_file(artifacts / "guidance" / "application.json")
    _exact_keys(
        application,
        {"schema_version", "run_id", "generation", "selection_digest", "obligations"},
        "guidance application",
    )
    if (
        application.get("schema_version") != APPLICATION_SCHEMA
        or application.get("run_id") != selected.run_id
        or application.get("generation") != selected.generation
        or application.get("selection_digest") != selected.selection_digest
    ):
        raise RunGuidanceError("guidance application belongs to another selection")
    rows = application.get("obligations")
    if not isinstance(rows, list):
        raise RunGuidanceError("guidance application obligations must be a list")
    expected = {
        "obligation_id",
        "disposition",
        "basis",
        "acceptance_obligation_ids",
        "process_checkpoint_ids",
        "construction_requirement_ids",
        "independent_review",
    }
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RunGuidanceError("guidance application row must be an object")
        _exact_keys(row, expected, "guidance application row")
        obligation_id = _guidance_id(row["obligation_id"], "application obligation id")
        obligation = selected.obligations.get(obligation_id)
        if obligation is None or obligation_id in indexed:
            raise RunGuidanceError(f"application has unknown or duplicate {obligation_id}")
        disposition = row["disposition"]
        if disposition not in {"applied", "not-applicable"}:
            raise RunGuidanceError(f"{obligation_id} has an invalid application disposition")
        _text(row["basis"], f"{obligation_id} application basis")
        acceptance = _id_list(row["acceptance_obligation_ids"], "acceptance obligation id")
        checkpoints = _id_list(row["process_checkpoint_ids"], "process checkpoint id")
        construction = _id_list(
            row["construction_requirement_ids"], "construction requirement id"
        )
        subject = obligation["subject_class"]
        if disposition == "not-applicable":
            if acceptance or checkpoints or construction:
                raise RunGuidanceError(f"{obligation_id} N/A disposition cannot retain bindings")
        elif subject == "behavioral":
            if not acceptance or checkpoints or construction:
                raise RunGuidanceError(f"{obligation_id} must bind acceptance obligations only")
        elif subject == "procedural":
            if acceptance or not checkpoints or construction:
                raise RunGuidanceError(f"{obligation_id} must bind process checkpoints only")
        elif acceptance or checkpoints or not construction:
            raise RunGuidanceError(
                f"{obligation_id} must bind architecture conformance requirements only"
            )
        application_subject = {
            key: value for key, value in row.items() if key != "independent_review"
        }
        _review(
            selected,
            row["independent_review"],
            obligation_id=obligation_id,
            application_subject_digest=_sha256(_canonical(application_subject)),
        )
        indexed[obligation_id] = row
    if list(indexed) != list(selected.obligations):
        raise RunGuidanceError("guidance application membership or order differs from selection")
    return GuidancePlan(selected, application, _sha256(raw), indexed)


def _binding_summary(row: Mapping[str, Any]) -> str:
    for field in (
        "acceptance_obligation_ids",
        "process_checkpoint_ids",
        "construction_requirement_ids",
    ):
        if row[field]:
            return json.dumps(row[field], sort_keys=True, ensure_ascii=False)
    return "[]"


def render_sections(plan: GuidancePlan) -> dict[str, str]:
    common = [
        f"Selection: `{plan.selected.selection_digest}`",
        f"Application: `{plan.application_digest}`",
        "Mechanical state: `routing-verified` (not a substantive compliance claim)",
        "",
    ]
    product = [
        MARKERS["product-specification.md"][0],
        "## Selected run guidance — behavior",
        "",
        *common,
    ]
    architecture = [
        MARKERS["architecture.md"][0],
        "## Selected run guidance — process and construction",
        "",
        *common,
    ]
    testing = [
        MARKERS["testing-strategy.md"][0],
        "## Selected run guidance — verification routing",
        "",
        *common,
    ]
    behavioral_count = 0
    for obligation_id, obligation in plan.selected.obligations.items():
        row = plan.application_rows[obligation_id]
        document = plan.selected.documents[obligation["source_name"]]
        route = ROUTES[obligation["subject_class"]]
        base = (
            f"source=`{obligation['source_name']}@{document['source_digest']}`; "
            f"class=`{obligation['subject_class']}`; kind=`{document['kind']}`; "
            f"disposition=`{row['disposition']}`; route=`{route}`"
        )
        if obligation["subject_class"] == "behavioral" and row["disposition"] == "applied":
            behavioral_count += 1
            product.extend(
                [
                    f"- **{obligation_id}** {obligation['text']}",
                    f"  - {base}",
                    f"  - Classification basis: {obligation['classification_basis']}",
                    f"  - Acceptance bindings: {_binding_summary(row)}",
                    "",
                ]
            )
        elif obligation["subject_class"] == "behavioral":
            product.append(f"- `{obligation_id}` not applicable — {row['basis']} ({base})")
        if "architecture" in obligation["authority_targets"]:
            architecture.extend(
                [
                    f"- **{obligation_id}** — {base}",
                    f"  - Obligation: {obligation['text']}",
                    f"  - Roles: {', '.join(obligation['roles'])}",
                    f"  - Classification basis: {obligation['classification_basis']}",
                    f"  - Application basis: {row['basis']}",
                    f"  - Bindings: {_binding_summary(row)}",
                    f"  - Independent review: `{row['independent_review']['digest']}`",
                    "",
                ]
            )
        testing.extend(
            [
                f"- **{obligation_id}** — {base}",
                f"  - Evidence route: {_binding_summary(row)}",
                f"  - Evidence class: `{row['disposition']}`",
                "",
            ]
        )
    if behavioral_count == 0:
        product.append("No selected guidance obligation currently creates Product behavior.")
    for name, lines in (
        ("product-specification.md", product),
        ("architecture.md", architecture),
        ("testing-strategy.md", testing),
    ):
        lines.extend([MARKERS[name][1], ""])
    return {
        "product-specification.md": "\n".join(product),
        "architecture.md": "\n".join(architecture),
        "testing-strategy.md": "\n".join(testing),
    }


def _replace_region(raw: bytes, *, name: str, section: str) -> bytes:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunGuidanceError(f"{name} is not UTF-8") from exc
    begin, end = MARKERS[name]
    counts = text.count(begin), text.count(end)
    if counts == (0, 0):
        return (text.rstrip("\n") + "\n\n" + section).encode("utf-8")
    if counts != (1, 1):
        raise RunGuidanceError(f"{name} guidance markers are malformed")
    start = text.index(begin)
    finish = text.index(end, start) + len(end)
    if finish < len(text) and text[finish] == "\n":
        finish += 1
    return (text[:start] + section + text[finish:]).encode("utf-8")


def update_artifacts(root: pathlib.Path, artifacts: pathlib.Path) -> GuidancePlan | None:
    plan = load_plan(root, artifacts)
    if plan is None:
        return None
    sections = render_sections(plan)
    pending: list[tuple[pathlib.Path, bytes, bytes]] = []
    for name, section in sections.items():
        path = artifacts / name
        digest_path = path.with_name(path.name + ".digest")
        if digest_path.exists() or digest_path.is_symlink():
            raise RunGuidanceError(f"refusing to rewrite ratified {name}; supersede it first")
        raw = _read_regular(path, ceiling=MAX_GUIDANCE_BYTES)
        pending.append((path, raw, _replace_region(raw, name=name, section=section)))
    for path, old, new in pending:
        _atomic_replace(path, old, new)
    return plan


def verify_plan(root: pathlib.Path, artifacts: pathlib.Path) -> GuidancePlan | None:
    plan = load_plan(root, artifacts)
    if plan is None:
        return None
    for name, section in render_sections(plan).items():
        raw = _read_regular(artifacts / name, ceiling=MAX_GUIDANCE_BYTES)
        if _replace_region(raw, name=name, section=section) != raw:
            raise RunGuidanceError(f"{name} guidance region differs from fresh derivation")
    return plan


def projection(plan: GuidancePlan, role: str) -> dict[str, Any]:
    if role not in ROLES:
        raise RunGuidanceError(f"unsupported guidance projection role: {role}")
    rows: list[dict[str, Any]] = []
    for obligation_id, obligation in plan.selected.obligations.items():
        application = plan.application_rows[obligation_id]
        if application["disposition"] != "applied" or role not in obligation["roles"]:
            continue
        if role == "tester" and obligation["subject_class"] != "behavioral":
            continue
        document = plan.selected.documents[obligation["source_name"]]
        rows.append(
            {
                "obligation_id": obligation_id,
                "source_name": obligation["source_name"],
                "source_digest": document["source_digest"],
                "kind": document["kind"],
                "text": obligation["text"],
                "subject_class": obligation["subject_class"],
                "classification_basis": obligation["classification_basis"],
                "enforcement_route": ROUTES[obligation["subject_class"]],
                "bindings": {
                    "acceptance_obligation_ids": application["acceptance_obligation_ids"],
                    "process_checkpoint_ids": application["process_checkpoint_ids"],
                    "construction_requirement_ids": application[
                        "construction_requirement_ids"
                    ],
                },
            }
        )
    return {
        "schema_version": PROJECTION_SCHEMA,
        "run_id": plan.selected.run_id,
        "generation": plan.selected.generation,
        "role": role,
        "selection_digest": plan.selected.selection_digest,
        "application_digest": plan.application_digest,
        "obligations": rows,
    }


def write_projection(
    root: pathlib.Path,
    artifacts: pathlib.Path,
    *,
    role: str,
    output: pathlib.Path,
) -> dict[str, Any]:
    plan = verify_plan(root, artifacts)
    if plan is None:
        return {
            "required": False,
            "schema_version": PROJECTION_SCHEMA,
            "path": None,
            "digest": None,
        }
    body = projection(plan, role)
    raw = _canonical(body)
    _install_exact(output, raw)
    return {
        "required": True,
        "schema_version": PROJECTION_SCHEMA,
        "path": str(output),
        "digest": _sha256(raw),
    }


def _raw_evidence(
    root: pathlib.Path,
    value: object,
    *,
    label: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise RunGuidanceError(f"{label} has no raw evidence")
    for number, reference in enumerate(value):
        _evidence_ref(root, reference, f"{label} raw evidence {number}")


def _finding_resolution(
    plan: GuidancePlan,
    reference: object,
    *,
    finding_id: str,
    candidate_sha: str,
) -> None:
    path, _address = _evidence_ref(
        plan.selected.root,
        reference,
        f"{finding_id} resolution",
    )
    value, _raw = _json_file(path)
    _exact_keys(
        value,
        {
            "schema_version",
            "run_id",
            "generation",
            "selection_digest",
            "application_digest",
            "finding_id",
            "candidate_sha",
            "verifier",
            "method",
            "resolved",
            "basis",
            "raw_evidence",
        },
        f"{finding_id} resolution",
    )
    if (
        value.get("schema_version") != FINDING_RESOLUTION_SCHEMA
        or value.get("run_id") != plan.selected.run_id
        or value.get("generation") != plan.selected.generation
        or value.get("selection_digest") != plan.selected.selection_digest
        or value.get("application_digest") != plan.application_digest
        or value.get("finding_id") != finding_id
        or value.get("candidate_sha") != candidate_sha
        or value.get("resolved") is not True
    ):
        raise RunGuidanceError(
            f"{finding_id} resolution is stale, failed, or belongs to another subject"
        )
    _text(value.get("verifier"), f"{finding_id} resolution verifier")
    if value.get("method") not in {
        "test",
        "static-analysis",
        "inspection",
        "process-receipt",
    }:
        raise RunGuidanceError(f"{finding_id} resolution has an invalid method")
    _text(value.get("basis"), f"{finding_id} resolution basis")
    _raw_evidence(
        plan.selected.root,
        value.get("raw_evidence"),
        label=f"{finding_id} resolution",
    )


def _finding_rows(
    plan: GuidancePlan,
    value: object,
    *,
    candidate_sha: str,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        raise RunGuidanceError("guidance findings must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    expected = {"finding_id", "severity", "status", "basis", "resolution_evidence"}
    for row in value:
        if not isinstance(row, dict):
            raise RunGuidanceError("guidance finding must be an object")
        _exact_keys(row, expected, "guidance finding")
        finding_id = _identifier(row["finding_id"], "guidance finding id")
        if finding_id in result:
            raise RunGuidanceError(f"duplicate guidance finding: {finding_id}")
        if row["severity"] not in {"critical", "standard", "cosmetic"}:
            raise RunGuidanceError(f"{finding_id} has invalid severity")
        if row["status"] not in {"open", "resolved"}:
            raise RunGuidanceError(f"{finding_id} has invalid status")
        _text(row["basis"], f"{finding_id} basis")
        if row["status"] == "resolved":
            _finding_resolution(
                plan,
                row["resolution_evidence"],
                finding_id=finding_id,
                candidate_sha=candidate_sha,
            )
        elif row["resolution_evidence"] is not None:
            raise RunGuidanceError(f"{finding_id} open finding cannot carry resolution evidence")
        result[finding_id] = row
    if list(result) != sorted(result):
        raise RunGuidanceError("guidance findings are not sorted")
    return result


def _observation(
    plan: GuidancePlan,
    reference: object,
    *,
    obligation_id: str,
    candidate_sha: str,
    number: int,
) -> None:
    path, _address = _evidence_ref(
        plan.selected.root,
        reference,
        f"{obligation_id} observation {number}",
    )
    value, _raw = _json_file(path)
    _exact_keys(
        value,
        {
            "schema_version",
            "run_id",
            "generation",
            "selection_digest",
            "application_digest",
            "obligation_id",
            "candidate_sha",
            "verifier",
            "method",
            "passed",
            "basis",
            "raw_evidence",
        },
        f"{obligation_id} observation {number}",
    )
    if (
        value.get("schema_version") != OBSERVATION_SCHEMA
        or value.get("run_id") != plan.selected.run_id
        or value.get("generation") != plan.selected.generation
        or value.get("selection_digest") != plan.selected.selection_digest
        or value.get("application_digest") != plan.application_digest
        or value.get("obligation_id") != obligation_id
        or value.get("candidate_sha") != candidate_sha
        or value.get("passed") is not True
    ):
        raise RunGuidanceError(
            f"{obligation_id} observation {number} is stale, failed, or belongs to another subject"
        )
    _text(value.get("verifier"), f"{obligation_id} observation verifier")
    if value.get("method") not in {"test", "static-analysis", "inspection", "process-receipt"}:
        raise RunGuidanceError(f"{obligation_id} observation {number} has an invalid method")
    _text(value.get("basis"), f"{obligation_id} observation basis")
    _raw_evidence(
        plan.selected.root,
        value.get("raw_evidence"),
        label=f"{obligation_id} observation {number}",
    )


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
        raise RunGuidanceError("guidance evidence candidate is not an exact commit id")
    value, raw = _json_file(artifacts / "guidance" / "evidence.json")
    _exact_keys(
        value,
        {
            "schema_version",
            "run_id",
            "generation",
            "selection_digest",
            "application_digest",
            "candidate_sha",
            "results",
            "findings",
        },
        "guidance evidence",
    )
    if (
        value.get("schema_version") != EVIDENCE_SCHEMA
        or value.get("run_id") != plan.selected.run_id
        or value.get("generation") != plan.selected.generation
        or value.get("selection_digest") != plan.selected.selection_digest
        or value.get("application_digest") != plan.application_digest
        or value.get("candidate_sha") != candidate_sha
    ):
        raise RunGuidanceError("guidance evidence is stale or belongs to another subject")
    _finding_rows(plan, value.get("findings"), candidate_sha=candidate_sha)
    rows = value.get("results")
    if not isinstance(rows, list):
        raise RunGuidanceError("guidance evidence results must be a list")
    actual: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RunGuidanceError("guidance evidence result must be an object")
        _exact_keys(
            row,
            {"obligation_id", "status", "closure_class", "basis", "evidence"},
            "guidance evidence result",
        )
        obligation_id = _guidance_id(row["obligation_id"], "guidance evidence obligation")
        actual.append(obligation_id)
        obligation = plan.selected.obligations.get(obligation_id)
        application = plan.application_rows.get(obligation_id)
        if obligation is None or application is None:
            raise RunGuidanceError(f"guidance evidence has unknown obligation: {obligation_id}")
        _text(row["basis"], f"{obligation_id} evidence basis")
        refs = row["evidence"]
        if not isinstance(refs, list):
            raise RunGuidanceError(f"{obligation_id} evidence references must be a list")
        for number, reference in enumerate(refs):
            _observation(
                plan,
                reference,
                obligation_id=obligation_id,
                candidate_sha=candidate_sha,
                number=number,
            )
        if application["disposition"] == "not-applicable":
            expected = ("not-applicable", "routed", False)
        else:
            expected = ("evidence-complete", "refused", True)
        if row["status"] != expected[0] or row["closure_class"] != expected[1]:
            raise RunGuidanceError(f"{obligation_id} has the wrong evidence state")
        if bool(refs) != expected[2]:
            raise RunGuidanceError(f"{obligation_id} has the wrong evidence membership")
    if actual != list(plan.selected.obligations):
        raise RunGuidanceError("guidance evidence membership or order differs from application")
    return {**value, "evidence_digest": _sha256(raw)}


def assessment_state(root: pathlib.Path, artifacts: pathlib.Path) -> dict[str, Any]:
    try:
        selected = load_selection(root)
    except (AgreementContractError, RunGuidanceError, OSError):
        return {
            "state": "noncompliant",
            "selection_digest": None,
            "application_digest": None,
            "evidence_digest": None,
            "findings": ["guidance-selection-invalid"],
        }
    if selected is None:
        return {
            "state": "none",
            "selection_digest": None,
            "application_digest": None,
            "evidence_digest": None,
            "findings": [],
        }
    try:
        plan = verify_plan(root, artifacts)
    except (AgreementContractError, RunGuidanceError, OSError):
        application_path = artifacts / "guidance" / "application.json"
        state = "pending-application" if not application_path.exists() else "noncompliant"
        state_findings = [] if state == "pending-application" else ["guidance-application-invalid"]
        return {
            "state": state,
            "selection_digest": selected.selection_digest,
            "application_digest": None,
            "evidence_digest": None,
            "findings": state_findings,
        }
    assert plan is not None
    evidence_path = artifacts / "guidance" / "evidence.json"
    if not evidence_path.exists() and not evidence_path.is_symlink():
        return {
            "state": "routing-verified",
            "selection_digest": selected.selection_digest,
            "application_digest": plan.application_digest,
            "evidence_digest": None,
            "findings": [],
        }
    try:
        evidence, _raw = _json_file(evidence_path)
        candidate_sha = evidence.get("candidate_sha")
        if not isinstance(candidate_sha, str):
            raise RunGuidanceError("guidance evidence has no candidate")
        verified = verify_evidence(root, artifacts, candidate_sha=candidate_sha)
        assert verified is not None
        verified_findings = _finding_rows(
            plan,
            verified["findings"],
            candidate_sha=candidate_sha,
        )
    except (AgreementContractError, RunGuidanceError, OSError):
        return {
            "state": "noncompliant",
            "selection_digest": selected.selection_digest,
            "application_digest": plan.application_digest,
            "evidence_digest": None,
            "findings": ["guidance-evidence-invalid"],
        }
    open_findings = [key for key, row in verified_findings.items() if row["status"] == "open"]
    return {
        "state": "noncompliant" if open_findings else "evidence-complete",
        "selection_digest": selected.selection_digest,
        "application_digest": plan.application_digest,
        "evidence_digest": verified["evidence_digest"],
        "findings": open_findings,
    }


def _summary(plan: GuidancePlan | None) -> dict[str, Any]:
    if plan is None:
        return {"required": False, "schema_version": CONTRACT_SCHEMA, "state": "none"}
    return {
        "required": True,
        "schema_version": CONTRACT_SCHEMA,
        "state": "routing-verified",
        "selection_digest": plan.selected.selection_digest,
        "application_digest": plan.application_digest,
        "obligation_count": len(plan.selected.obligations),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    admission = subparsers.add_parser("admit")
    admission.add_argument("--root", type=pathlib.Path, required=True)
    admission.add_argument("--run-id", required=True)
    admission.add_argument("--generation", type=int, required=True)
    admission.add_argument("--config-source", action="append", default=[])
    admission.add_argument("--config-digest", action="append", default=[])
    for command in ("update-artifacts", "verify-plan", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--root", type=pathlib.Path, required=True)
        child.add_argument("--artifacts", type=pathlib.Path, required=True)
    projected = subparsers.add_parser("projection")
    projected.add_argument("--root", type=pathlib.Path, required=True)
    projected.add_argument("--artifacts", type=pathlib.Path, required=True)
    projected.add_argument("--role", choices=ROLES, required=True)
    projected.add_argument("--output", type=pathlib.Path, required=True)
    evidence = subparsers.add_parser("verify-evidence")
    evidence.add_argument("--root", type=pathlib.Path, required=True)
    evidence.add_argument("--artifacts", type=pathlib.Path, required=True)
    evidence.add_argument("--candidate-sha", required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "admit":
            result = admit(
                arguments.root,
                run_id=arguments.run_id,
                generation=arguments.generation,
                config_sources=arguments.config_source,
                config_digests=arguments.config_digest,
            )
        elif arguments.command == "update-artifacts":
            result = _summary(update_artifacts(arguments.root, arguments.artifacts))
        elif arguments.command == "verify-plan":
            result = _summary(verify_plan(arguments.root, arguments.artifacts))
        elif arguments.command == "projection":
            result = write_projection(
                arguments.root,
                arguments.artifacts,
                role=arguments.role,
                output=arguments.output,
            )
        elif arguments.command == "verify-evidence":
            verified = verify_evidence(
                arguments.root,
                arguments.artifacts,
                candidate_sha=arguments.candidate_sha,
            )
            result = {"required": verified is not None, "verified": verified is not None}
        else:
            result = assessment_state(arguments.root, arguments.artifacts)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    except (AgreementContractError, RunGuidanceError, OSError) as exc:
        print(f"run guidance refused: {exc}", file=sys.stderr)
        return 71
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
