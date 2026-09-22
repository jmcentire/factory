#!/usr/bin/env python3
"""Conserve extracted semantic evidence into an exact signed-spec checklist.

The deterministic guarantee starts after extraction: this program cannot decide
whether prose contains an ambiguity or whether a ruling is wise.  It can prove
that every retained source has two separately recorded, source-bound extraction
manifests; that no extracted observation was collapsed or dropped; and that the
Product Specification contains the exact resulting checklist and input-closure
digest.  Extractor provenance is retained as a claim, not treated as authenticated.
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
from dataclasses import dataclass
from typing import Any

PROTOCOL = "factory-semantic-evidence-union/1"
EXTRACTION_SCHEMA = "factory-semantic-extraction/1"
RULINGS_SCHEMA = "factory-semantic-rulings/1"
SOURCE_KINDS = ("planning-pass", "lane-trace", "adversarial-review")
MIN_EXTRACTIONS = 2
PRODUCER_ENROLLMENT_COVERAGE = "unknown-until-producer-inventory-is-joined"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
BEGIN = "<!-- FACTORY-SEMANTIC-UNION:BEGIN -->"
END = "<!-- FACTORY-SEMANTIC-UNION:END -->"
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class SemanticUnionError(RuntimeError):
    """The evidence set cannot produce a closed, deterministic semantic union."""


@dataclass(frozen=True)
class Observation:
    observation_id: str
    source_kind: str
    source_id: str
    source_digest: str
    start: int
    end: int
    scope: str
    question: str
    extractors: tuple[str, ...]


@dataclass(frozen=True)
class UnionResult:
    observations: tuple[Observation, ...]
    rulings: dict[str, dict[str, Any]]
    input_closure: dict[str, Any]
    input_closure_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _read_regular(path: pathlib.Path, *, ceiling: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SemanticUnionError(f"cannot open regular input {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SemanticUnionError(f"input is not a regular file: {path}")
        if before.st_size <= 0 or before.st_size > ceiling:
            raise SemanticUnionError(f"input is empty or exceeds its byte ceiling: {path}")
        chunks: list[bytes] = []
        read = 0
        while chunk := os.read(descriptor, min(1024 * 1024, ceiling + 1)):
            chunks.append(chunk)
            read += len(chunk)
            if read > ceiling:
                raise SemanticUnionError(f"input exceeds its byte ceiling: {path}")
        after = os.fstat(descriptor)

        def identity(item: os.stat_result) -> tuple[int, int, int, int, int, int]:
            return (
                item.st_dev,
                item.st_ino,
                item.st_mode,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if identity(before) != identity(after):
            raise SemanticUnionError(f"input changed while read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _json_file(path: pathlib.Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, ceiling=MAX_MANIFEST_BYTES)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticUnionError(f"invalid JSON manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SemanticUnionError(f"JSON manifest must be an object: {path}")
    return value, raw


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise SemanticUnionError(
            f"{label} keys differ: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )


def _single_line(value: object, label: str, *, minimum: int = 1, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise SemanticUnionError(f"{label} must be a bounded non-empty string")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise SemanticUnionError(f"{label} must be one line")
    return value


def _safe_directory(path: pathlib.Path, label: str) -> list[pathlib.Path]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SemanticUnionError(f"missing {label}: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SemanticUnionError(f"{label} is not a real directory: {path}")
    entries = sorted(path.iterdir(), key=lambda item: item.name)
    if any(entry.is_symlink() for entry in entries):
        raise SemanticUnionError(f"{label} contains a symlink")
    return entries


def derive_observation_id(
    *,
    source_kind: str,
    source_id: str,
    source_digest: str,
    start: int,
    end: int,
    scope: str,
    question: str,
) -> str:
    """Derive identity from exact evidence; callers cannot author a collapsing merge key."""

    subject = {
        "source_kind": source_kind,
        "source_id": source_id,
        "source_digest": source_digest,
        "start": start,
        "end": end,
        "scope": scope,
        "question": question,
    }
    return "observation-" + hashlib.sha256(_canonical(subject)).hexdigest()


def _source_files(evidence_root: pathlib.Path) -> dict[tuple[str, str], tuple[pathlib.Path, bytes]]:
    sources_root = evidence_root / "sources"
    root_entries = _safe_directory(sources_root, "semantic source directory")
    if {entry.name for entry in root_entries} != set(SOURCE_KINDS):
        raise SemanticUnionError(
            "semantic source directory must contain exactly the closed source-kind set"
        )
    sources: dict[tuple[str, str], tuple[pathlib.Path, bytes]] = {}
    for kind in SOURCE_KINDS:
        for path in _safe_directory(sources_root / kind, f"{kind} source directory"):
            if not path.name.endswith(".source") or not path.is_file():
                raise SemanticUnionError(f"unexpected semantic source entry: {path}")
            source_id = path.name.removesuffix(".source")
            if not _SAFE_ID.fullmatch(source_id):
                raise SemanticUnionError(f"unsafe semantic source id: {source_id!r}")
            sources[(kind, source_id)] = (
                path,
                _read_regular(path, ceiling=MAX_SOURCE_BYTES),
            )
    if not sources:
        raise SemanticUnionError("semantic evidence has no enrolled source")
    return sources


def _items(
    manifest: dict[str, Any],
    *,
    source_size: int,
    source_kind: str,
    source_id: str,
    source_digest: str,
) -> list[dict[str, Any]]:
    items = manifest["items"]
    if not isinstance(items, list):
        raise SemanticUnionError("extraction items must be a list")
    parsed_items: list[dict[str, Any]] = []
    item_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SemanticUnionError(f"extraction item {index} must be an object")
        _exact_keys(item, {"start", "end", "scope", "question"}, f"extraction item {index}")
        start, end = item["start"], item["end"]
        if not isinstance(start, int) or isinstance(start, bool):
            raise SemanticUnionError(f"extraction item {index} start must be an integer")
        if not isinstance(end, int) or isinstance(end, bool) or not 0 <= start < end <= source_size:
            raise SemanticUnionError(f"extraction item {index} has an invalid source range")
        scope = _single_line(item["scope"], f"extraction item {index} scope", maximum=500)
        question = _single_line(item["question"], f"extraction item {index} question", minimum=8)
        observation_id = derive_observation_id(
            source_kind=source_kind,
            source_id=source_id,
            source_digest=source_digest,
            start=start,
            end=end,
            scope=scope,
            question=question,
        )
        if observation_id in item_ids:
            raise SemanticUnionError("an extraction manifest repeats one observation")
        item_ids.add(observation_id)
        parsed_items.append(
            {
                "start": start,
                "end": end,
                "scope": scope,
                "question": question,
                "observation_id": observation_id,
            }
        )
    return parsed_items


def _load_extractions(
    evidence_root: pathlib.Path,
    sources: dict[tuple[str, str], tuple[pathlib.Path, bytes]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    extraction_root = evidence_root / "extractions"
    root_entries = _safe_directory(extraction_root, "semantic extraction directory")
    if {entry.name for entry in root_entries} != set(SOURCE_KINDS):
        raise SemanticUnionError(
            "semantic extraction directory must contain exactly the closed source-kind set"
        )
    observations: dict[str, dict[str, Any]] = {}
    extraction_closure: list[dict[str, Any]] = []
    seen_source_dirs: set[tuple[str, str]] = set()
    for kind in SOURCE_KINDS:
        for source_dir in _safe_directory(extraction_root / kind, f"{kind} extraction directory"):
            if not source_dir.is_dir() or not _SAFE_ID.fullmatch(source_dir.name):
                raise SemanticUnionError(f"unexpected semantic extraction entry: {source_dir}")
            key = (kind, source_dir.name)
            if key not in sources:
                raise SemanticUnionError(
                    f"extraction exists for an unenrolled source: {kind}/{source_dir.name}"
                )
            seen_source_dirs.add(key)
            _source_path, source_raw = sources[key]
            source_digest = _sha256(source_raw)
            manifests = _safe_directory(source_dir, f"extractions for {kind}/{source_dir.name}")
            if len(manifests) < MIN_EXTRACTIONS:
                raise SemanticUnionError(
                    f"{kind}/{source_dir.name} has fewer than {MIN_EXTRACTIONS} "
                    "separately recorded extractions"
                )
            extractor_identities: set[tuple[str, str, str]] = set()
            for path in manifests:
                if path.suffix != ".json" or not path.is_file():
                    raise SemanticUnionError(f"unexpected extraction manifest entry: {path}")
                manifest, raw = _json_file(path)
                _exact_keys(
                    manifest,
                    {
                        "schema_version",
                        "source_kind",
                        "source_id",
                        "source_sha256",
                        "extractor",
                        "items",
                    },
                    f"extraction manifest {path}",
                )
                if manifest["schema_version"] != EXTRACTION_SCHEMA:
                    raise SemanticUnionError(f"unsupported extraction schema in {path}")
                if manifest["source_kind"] != kind or manifest["source_id"] != source_dir.name:
                    raise SemanticUnionError(f"extraction source identity differs in {path}")
                if manifest["source_sha256"] != source_digest:
                    raise SemanticUnionError(f"extraction source digest differs in {path}")
                extractor = manifest["extractor"]
                if not isinstance(extractor, dict):
                    raise SemanticUnionError(f"extractor identity must be an object in {path}")
                _exact_keys(
                    extractor,
                    {"id", "version", "configuration_digest"},
                    f"extractor identity in {path}",
                )
                extractor_id = _single_line(extractor["id"], "extractor id", maximum=200)
                version = _single_line(extractor["version"], "extractor version", maximum=200)
                configuration_digest = extractor["configuration_digest"]
                if not isinstance(configuration_digest, str) or not _DIGEST.fullmatch(
                    configuration_digest
                ):
                    raise SemanticUnionError(f"extractor configuration digest is invalid in {path}")
                identity = (extractor_id, version, configuration_digest)
                if identity in extractor_identities:
                    raise SemanticUnionError(
                        f"duplicate extractor identity for {kind}/{source_dir.name}"
                    )
                extractor_identities.add(identity)
                items = _items(
                    manifest,
                    source_size=len(source_raw),
                    source_kind=kind,
                    source_id=source_dir.name,
                    source_digest=source_digest,
                )
                extraction_label = f"{extractor_id}@{version}:{configuration_digest}"
                for item in items:
                    observation_id = item["observation_id"]
                    subject = {
                        "source_kind": kind,
                        "source_id": source_dir.name,
                        "source_digest": source_digest,
                        "start": item["start"],
                        "end": item["end"],
                        "scope": item["scope"],
                        "question": item["question"],
                    }
                    existing = observations.get(observation_id)
                    if existing is not None and existing["subject"] != subject:
                        raise SemanticUnionError("derived observation id collision")
                    if existing is None:
                        observations[observation_id] = {"subject": subject, "extractors": []}
                    observations[observation_id]["extractors"].append(extraction_label)
                extraction_closure.append(
                    {
                        "source_kind": kind,
                        "source_id": source_dir.name,
                        "manifest": path.name,
                        "manifest_digest": _sha256(raw),
                        "extractor": dict(extractor),
                    }
                )
    missing = set(sources) - seen_source_dirs
    if missing:
        rendered = ", ".join(f"{kind}/{source}" for kind, source in sorted(missing))
        raise SemanticUnionError(f"enrolled source has no extraction directory: {rendered}")
    return observations, extraction_closure


def _load_rulings(
    evidence_root: pathlib.Path, observation_ids: set[str]
) -> tuple[dict[str, dict[str, Any]], str]:
    value, raw = _json_file(evidence_root / "rulings.json")
    _exact_keys(value, {"schema_version", "items"}, "semantic rulings")
    if value["schema_version"] != RULINGS_SCHEMA or not isinstance(value["items"], list):
        raise SemanticUnionError("semantic rulings have an unsupported schema or item set")
    rulings: dict[str, dict[str, Any]] = {}
    keys = {
        "observation_id",
        "status",
        "disposition",
        "ruling",
        "authority_basis",
        "owner",
        "next_action",
    }
    for number, item in enumerate(value["items"]):
        if not isinstance(item, dict):
            raise SemanticUnionError(f"semantic ruling {number} must be an object")
        _exact_keys(item, keys, f"semantic ruling {number}")
        observation_id = item["observation_id"]
        if not isinstance(observation_id, str) or not observation_id.startswith("observation-"):
            raise SemanticUnionError(f"semantic ruling {number} has an invalid observation id")
        if observation_id in rulings:
            raise SemanticUnionError(f"semantic ruling repeats {observation_id}")
        status = item["status"]
        disposition = item["disposition"]
        _single_line(item["ruling"], f"semantic ruling {number} text", minimum=12)
        authority = item["authority_basis"]
        owner = item["owner"]
        next_action = item["next_action"]
        if status == "closed":
            if disposition not in {"resolved", "not-an-ambiguity"}:
                raise SemanticUnionError("closed semantic ruling has an invalid disposition")
            _single_line(authority, f"semantic ruling {number} authority basis", minimum=8)
            if owner is not None or next_action is not None:
                raise SemanticUnionError("closed semantic ruling cannot retain open-work fields")
        elif status == "open":
            if disposition != "deferred" or authority is not None:
                raise SemanticUnionError("open semantic ruling must be deferred without authority")
            _single_line(owner, f"semantic ruling {number} owner", minimum=2, maximum=200)
            _single_line(next_action, f"semantic ruling {number} next action", minimum=8)
        else:
            raise SemanticUnionError("semantic ruling status must be open or closed")
        rulings[observation_id] = dict(item)
    missing = observation_ids - set(rulings)
    extra = set(rulings) - observation_ids
    if missing or extra:
        raise SemanticUnionError(
            f"semantic ruling set differs from the evidence union: "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )
    return rulings, _sha256(raw)


def load_union(artifacts: pathlib.Path) -> UnionResult:
    evidence_root = artifacts / "semantic-evidence"
    evidence_entries = _safe_directory(evidence_root, "semantic evidence root")
    expected_entries = {"sources", "extractions", "rulings.json"}
    if {entry.name for entry in evidence_entries} != expected_entries:
        raise SemanticUnionError(
            "semantic evidence root must contain exactly sources, extractions, and rulings.json"
        )
    sources = _source_files(evidence_root)
    observed, extraction_closure = _load_extractions(evidence_root, sources)
    rulings, rulings_digest = _load_rulings(evidence_root, set(observed))
    observations = tuple(
        Observation(
            observation_id=observation_id,
            extractors=tuple(sorted(set(value["extractors"]))),
            **value["subject"],
        )
        for observation_id, value in sorted(observed.items())
    )
    source_closure = [
        {
            "source_kind": kind,
            "source_id": source_id,
            "source_digest": _sha256(raw),
            "source_bytes": len(raw),
        }
        for (kind, source_id), (_path, raw) in sorted(sources.items())
    ]
    compiler_digest = _sha256(_read_regular(pathlib.Path(__file__), ceiling=MAX_MANIFEST_BYTES))
    input_closure = {
        "schema_version": PROTOCOL,
        "compiler_digest": compiler_digest,
        "producer_enrollment_coverage": PRODUCER_ENROLLMENT_COVERAGE,
        "minimum_separately_recorded_extractions": MIN_EXTRACTIONS,
        "sources": source_closure,
        "extractions": sorted(
            extraction_closure,
            key=lambda row: (row["source_kind"], row["source_id"], row["manifest"]),
        ),
        "rulings_digest": rulings_digest,
    }
    return UnionResult(
        observations=observations,
        rulings=rulings,
        input_closure=input_closure,
        input_closure_digest=_sha256(_canonical(input_closure)),
    )


def render_section(result: UnionResult) -> str:
    lines = [
        BEGIN,
        "## Semantic evidence union (generated; do not hand-edit)",
        "",
        f"Protocol: `{PROTOCOL}`",
        f"Input closure: `{result.input_closure_digest}`",
        f"Producer enrollment coverage: `{result.input_closure['producer_enrollment_coverage']}`",
        "",
    ]
    if not result.observations:
        lines.append("No semantic observations were extracted from the enrolled sources.")
    for observation in result.observations:
        ruling = result.rulings[observation.observation_id]
        checked = "x" if ruling["status"] == "closed" else " "
        lines.extend(
            [
                f"- [{checked}] `{observation.observation_id}` — {ruling['disposition']}",
                f"  - Scope: {json.dumps(observation.scope, ensure_ascii=False)}",
                f"  - Question: {json.dumps(observation.question, ensure_ascii=False)}",
                "  - Evidence: "
                f"`{observation.source_kind}/{observation.source_id}"
                f"@{observation.start}:{observation.end}`; "
                f"extractors={len(observation.extractors)}",
                f"  - Ruling: {json.dumps(ruling['ruling'], ensure_ascii=False)}",
            ]
        )
        if ruling["status"] == "closed":
            lines.append(
                f"  - Authority basis: {json.dumps(ruling['authority_basis'], ensure_ascii=False)}"
            )
        else:
            lines.append(
                f"  - Open work: owner={json.dumps(ruling['owner'], ensure_ascii=False)}; "
                f"next={json.dumps(ruling['next_action'], ensure_ascii=False)}"
            )
    lines.extend(["", END, ""])
    return "\n".join(lines)


def _spec_section(raw: str) -> str:
    if raw.count(BEGIN) != 1 or raw.count(END) != 1:
        raise SemanticUnionError("Product Specification must contain exactly one semantic union")
    start = raw.index(BEGIN)
    try:
        finish = raw.index(END, start) + len(END)
    except ValueError as exc:
        raise SemanticUnionError(
            "Product Specification semantic union markers are reversed"
        ) from exc
    if finish < len(raw) and raw[finish] == "\n":
        finish += 1
    return raw[start:finish]


def verify_spec(artifacts: pathlib.Path, spec: pathlib.Path) -> UnionResult:
    result = load_union(artifacts)
    try:
        spec_text = _read_regular(spec, ceiling=MAX_SOURCE_BYTES).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticUnionError("Product Specification is not UTF-8") from exc
    expected = render_section(result)
    actual = _spec_section(spec_text)
    if actual != expected:
        raise SemanticUnionError(
            "Product Specification semantic union differs from fresh evidence derivation"
        )
    open_ids = [
        observation.observation_id
        for observation in result.observations
        if result.rulings[observation.observation_id]["status"] == "open"
    ]
    if open_ids:
        raise SemanticUnionError(f"semantic evidence union has open items: {open_ids}")
    return result


def _replace_spec(raw: str, section: str) -> str:
    begin_count, end_count = raw.count(BEGIN), raw.count(END)
    if (begin_count, end_count) == (0, 0):
        return raw.rstrip("\n") + "\n\n" + section
    if (begin_count, end_count) != (1, 1):
        raise SemanticUnionError("Product Specification has malformed semantic union markers")
    start = raw.index(BEGIN)
    try:
        finish = raw.index(END, start) + len(END)
    except ValueError as exc:
        raise SemanticUnionError(
            "Product Specification semantic union markers are reversed"
        ) from exc
    if finish < len(raw) and raw[finish] == "\n":
        finish += 1
    return raw[:start] + section + raw[finish:]


def update_spec(artifacts: pathlib.Path, spec: pathlib.Path) -> UnionResult:
    digest_path = spec.with_name(spec.name + ".digest")
    if digest_path.exists() or digest_path.is_symlink():
        raise SemanticUnionError(
            "refusing to rewrite a ratified Product Specification; supersede it before signing"
        )
    result = load_union(artifacts)
    try:
        old_raw = _read_regular(spec, ceiling=MAX_SOURCE_BYTES)
        old = old_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticUnionError("Product Specification is not UTF-8") from exc
    new = _replace_spec(old, render_section(result)).encode("utf-8")
    temporary = tempfile.NamedTemporaryFile(dir=spec.parent, delete=False)
    try:
        temporary.write(new)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        if _read_regular(spec, ceiling=MAX_SOURCE_BYTES) != old_raw:
            raise SemanticUnionError("Product Specification changed before atomic replacement")
        os.chmod(temporary.name, stat.S_IMODE(spec.stat().st_mode))
        os.replace(temporary.name, spec)
        directory = os.open(spec.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.close()
        try:
            os.unlink(temporary.name)
        except FileNotFoundError:
            pass
        raise
    return result


def _summary(result: UnionResult) -> dict[str, Any]:
    open_count = sum(
        result.rulings[item.observation_id]["status"] == "open" for item in result.observations
    )
    return {
        "schema_version": PROTOCOL,
        "input_closure_digest": result.input_closure_digest,
        "producer_enrollment_coverage": result.input_closure["producer_enrollment_coverage"],
        "source_count": len(result.input_closure["sources"]),
        "extraction_count": len(result.input_closure["extractions"]),
        "observation_count": len(result.observations),
        "open_count": open_count,
        "closed_count": len(result.observations) - open_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("inspect", "render", "update-spec", "verify"))
    parser.add_argument("--artifacts", type=pathlib.Path, required=True)
    parser.add_argument("--spec", type=pathlib.Path)
    arguments = parser.parse_args()
    try:
        if arguments.command in {"update-spec", "verify"} and arguments.spec is None:
            raise SemanticUnionError(f"{arguments.command} requires --spec")
        if arguments.command == "render":
            result = load_union(arguments.artifacts)
            sys.stdout.write(render_section(result))
        elif arguments.command == "update-spec":
            assert arguments.spec is not None
            result = update_spec(arguments.artifacts, arguments.spec)
            print(json.dumps(_summary(result), sort_keys=True, separators=(",", ":")))
        elif arguments.command == "verify":
            assert arguments.spec is not None
            result = verify_spec(arguments.artifacts, arguments.spec)
            print(json.dumps(_summary(result), sort_keys=True, separators=(",", ":")))
        else:
            result = load_union(arguments.artifacts)
            print(json.dumps(_summary(result), sort_keys=True, separators=(",", ":")))
    except (OSError, SemanticUnionError) as exc:
        print(f"semantic-union refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
