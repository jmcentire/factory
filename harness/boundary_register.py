#!/usr/bin/env python3
"""Verify that every identifier crossing a boundary is the same literal on both sides.

The invariant this module upholds: **no identifier that crosses a boundary is assumed.**
A database role, a grant, a service principal, a token audience, a metric type, a log
field, an environment variable, a service name: each one is written down by two parties
that do not share a compiler. When one party names it from memory, the two drift and
nothing local fails. The founder named the pattern directly (2026-10-14):

    "Mismatched identifiers, names, tokens, etc across boundaries. ... Some worker is
    presumed to exist with access and simply doesn't. Another service is presumed to
    recognize your credentials but doesn't. Those aren't acceptable."

So the register is a set of entries, one per boundary identifier. Each entry names every
party that touches the identifier, the exact literal each party uses, and at least one
**record** per party: a file line or a captured live observation, bound by its SHA-256.
The verifier re-reads every record, refuses a stale digest, and requires the literal to
occur in it as a whole token. It then decides the entry's relation:

``equal``
    every party uses the identical literal (case-sensitive), with at least one party that
    defines or grants it and one that consumes or presents it.
``absent``
    an isolation boundary: the presenting subject (a principal, a key) must NOT occur in
    any denying party's record. This is how "service A cannot reach service B" is proven
    from the access policy actually in force, not from the policy someone meant to write.

A register is per run (``artifacts/boundary-register.json``). It is testing IR derived
for the Architecture Specification's trust and integration boundaries, not a fourth
intent authority. The format (``boundary-register/1``) is shared with Pact and Constrain;
this harness adds only an optional ``root`` per record (``target`` or ``run``), because a
factory run holds evidence outside the target tree.

The verifier proves the literals agree with the records. It cannot prove the register
lists every boundary the system crosses; that completeness is a judgment surface, which
the Orchestrator and Validator check (``docs/practices/boundary-identifiers.md``).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SCHEMA = "boundary-register/1"
REGISTER_NAME = "boundary-register.json"
KINDS = frozenset(
    {
        "name",
        "endpoint",
        "credential",
        "audience",
        "principal",
        "grant",
        "role",
        "table",
        "column",
        "field",
        "metric",
        "label",
        "env",
        "config-key",
        "topic",
        "queue",
        "bucket",
        "secret",
        "service",
        "header",
        "claim",
        "enum",
        "other",
    }
)
PRODUCER_ROLES = frozenset({"defines", "grants"})
CONSUMER_ROLES = frozenset({"consumes", "presents"})
ROLES = PRODUCER_ROLES | CONSUMER_ROLES | {"denies"}
RELATIONS = frozenset({"equal", "absent"})
ROOTS = frozenset({"target", "run"})
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_REGISTER_BYTES = 4 * 1024 * 1024

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_PLACEHOLDER = re.compile(
    r"^(?:todo|tbd|tbc|unknown|assumed|placeholder|changeme|n/?a|none|null|x{3,}|\?+)$",
    re.IGNORECASE,
)
_ANGLE_PLACEHOLDER = re.compile(r"<[^<>\s]+>")
_SEPARATORS = re.compile(r"[-_.\s]")


class BoundaryRegisterError(RuntimeError):
    """The register itself is unreadable or malformed; nothing in it can be trusted."""


@dataclass(frozen=True)
class Finding:
    """One reason a register entry does not hold."""

    entry: str
    party: str
    reason: str

    def render(self) -> str:
        where = f"{self.entry}" + (f" [{self.party}]" if self.party else "")
        return f"{where}: {self.reason}"


@dataclass(frozen=True)
class Report:
    """The verifier's verdict over one register."""

    register_digest: str
    entries: int
    findings: tuple[Finding, ...]
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def contains_token(haystack: str, value: str) -> bool:
    """Return whether ``value`` occurs in ``haystack`` as a whole token.

    A value that begins (ends) with an identifier character must not be preceded
    (followed) by one, so ``svc-auth`` is not found inside ``svc-auth-runtime`` and
    ``level`` is found inside ``"level":``. A value with punctuation at an edge matches
    at that edge unconditionally, because the punctuation is itself the boundary.
    """

    if not value:
        return False
    start = 0
    while True:
        index = haystack.find(value, start)
        if index < 0:
            return False
        end = index + len(value)
        before = (
            value[0] not in _TOKEN_CHARS or index == 0 or haystack[index - 1] not in _TOKEN_CHARS
        )
        after = (
            value[-1] not in _TOKEN_CHARS
            or end == len(haystack)
            or haystack[end] not in _TOKEN_CHARS
        )
        if before and after:
            return True
        start = index + 1


def is_placeholder(value: str) -> bool:
    """Return whether a value is a stand-in for a literal nobody looked up."""

    text = value.strip()
    return bool(_PLACEHOLDER.match(text) or _ANGLE_PLACEHOLDER.search(text))


def near_miss(values: Sequence[str]) -> bool:
    """Return whether distinct values collapse to one under case and separator folding."""

    folded = {_SEPARATORS.sub("", value).lower() for value in values}
    return len(set(values)) > 1 and len(folded) == 1


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path: pathlib.Path, *, ceiling: int) -> bytes:
    """Read one bounded regular file without following a final symlink."""

    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"not a regular file: {path}")
        if metadata.st_size > ceiling:
            raise OSError(f"exceeds {ceiling} bytes: {path}")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > ceiling:
                raise OSError(f"exceeds {ceiling} bytes: {path}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _resolve(base: pathlib.Path, relative: str) -> pathlib.Path:
    """Resolve a record path strictly inside ``base``."""

    candidate = pathlib.PurePosixPath(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"record path must be relative without '..': {relative!r}")
    resolved = (base / pathlib.Path(*candidate.parts)).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError(f"record path escapes its root: {relative!r}")
    return resolved


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() and "\x00" not in value else None


@dataclass(frozen=True)
class Roots:
    """Where record paths resolve. ``target`` is absent when no checkout was supplied."""

    run: pathlib.Path
    target: pathlib.Path | None
    candidate: bool = False


def _check_record(
    record: object, value: str, roots: Roots, *, must_contain: bool
) -> tuple[str | None, str | None, str | None]:
    """Verify one record. Return ``(failure, note, decoded_text)``."""

    if not isinstance(record, Mapping):
        return "record is not an object", None, None
    kind = record.get("kind")
    if kind not in {"file", "observation"}:
        return f"record kind must be file or observation, not {kind!r}", None, None
    root_name = record.get("root", "target" if kind == "file" else "run")
    if root_name not in ROOTS:
        return f"record root must be target or run, not {root_name!r}", None, None
    path_text = _text(record.get("path"))
    digest = record.get("sha256")
    if path_text is None:
        return "record has no path", None, None
    if not isinstance(digest, str) or not _HEX.match(digest):
        return f"record {path_text} has no 64-hex sha256", None, None
    line = record.get("line")
    if kind == "file":
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            return f"file record {path_text} needs a 1-based line", None, None
    else:
        if _text(record.get("command")) is None:
            return f"observation {path_text} does not say what command produced it", None, None
        observed = _text(record.get("observed_at"))
        try:
            parsed = datetime.datetime.fromisoformat(observed or "")
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is None:
            return f"observation {path_text} needs an RFC 3339 observed_at", None, None
    base = roots.run if root_name == "run" else roots.target
    if base is None:
        return f"record {path_text} cites the target tree but no checkout was supplied", None, None
    try:
        raw = _read_regular(_resolve(base, path_text), ceiling=MAX_RECORD_BYTES)
    except (OSError, ValueError) as exc:
        return f"record {path_text} is unreadable: {exc}", None, None
    text = raw.decode("utf-8", errors="surrogateescape")
    moved = None
    if _sha256(raw) != digest:
        # At endgame the candidate may legitimately have edited a cited target file. The
        # literal must still be in it; a candidate that dropped or renamed the literal is
        # exactly the drift this register exists to catch.
        if roots.candidate and root_name == "target" and kind == "file":
            if must_contain and not contains_token(text, value):
                return f"candidate changed {path_text} and {value!r} is no longer in it", None, text
            moved = f"{path_text} changed in the candidate; {value!r} still present"
            return None, moved, text
        return (
            f"record {path_text} is stale: sha256 {_sha256(raw)} != recorded {digest}; "
            "re-derive the record from the source, never edit the register to fit",
            None,
            None,
        )
    if must_contain:
        if kind == "file":
            lines = text.splitlines()
            assert isinstance(line, int)
            if line > len(lines):
                return f"{path_text} has {len(lines)} lines; cited line {line}", None, text
            if not contains_token(lines[line - 1], value):
                return f"{path_text}:{line} does not contain {value!r}", None, text
        elif not contains_token(text, value):
            return f"observation {path_text} does not contain {value!r}", None, text
    return None, moved, text


def _check_entry(entry: object, index: int, roots: Roots) -> tuple[list[Finding], list[str], str]:
    if not isinstance(entry, Mapping):
        return [Finding(f"entry[{index}]", "", "entry is not an object")], [], f"entry[{index}]"
    entry_id = entry.get("id")
    label = entry_id if isinstance(entry_id, str) and entry_id else f"entry[{index}]"
    findings: list[Finding] = []
    notes: list[str] = []

    def fail(party: str, reason: str) -> None:
        findings.append(Finding(label, party, reason))

    if not isinstance(entry_id, str) or not _ID.match(entry_id):
        fail("", "id must match [A-Za-z0-9][A-Za-z0-9._-]*")
    if entry.get("kind") not in KINDS:
        fail("", f"kind {entry.get('kind')!r} is not one of {sorted(KINDS)}")
    relation = entry.get("relation")
    if relation not in RELATIONS:
        fail("", f"relation must be equal or absent, not {relation!r}")
    if _text(entry.get("description")) is None:
        fail("", "description must say why the sides must agree")
    sides = entry.get("sides")
    if not isinstance(sides, list) or len(sides) < 2:
        fail("", "an entry needs at least two sides; a boundary has two parties")
        return findings, notes, label

    checked: list[tuple[str, str, str, list[str]]] = []  # party, role, value, record texts
    for position, side in enumerate(sides):
        if not isinstance(side, Mapping):
            fail(f"side[{position}]", "side is not an object")
            continue
        party = _text(side.get("party")) or f"side[{position}]"
        role = side.get("role")
        value = side.get("value")
        if role not in ROLES:
            fail(party, f"role {role!r} is not one of {sorted(ROLES)}")
            continue
        if not isinstance(value, str) or not value or "\x00" in value:
            fail(party, "value must be the exact non-empty literal this party uses")
            continue
        if is_placeholder(value):
            fail(party, f"value {value!r} is a placeholder, not a looked-up literal")
        records = side.get("records")
        if not isinstance(records, list) or not records:
            fail(party, f"UNVERIFIED: no record shows where {value!r} comes from")
            continue
        texts: list[str] = []
        for record in records:
            failure, note, text = _check_record(record, value, roots, must_contain=role != "denies")
            if failure:
                fail(party, failure)
            if note:
                notes.append(f"{label} [{party}]: {note}")
            if text is not None:
                texts.append(text)
        checked.append((party, str(role), value, texts))

    roles = [role for _party, role, _value, _texts in checked]
    if relation == "equal":
        if "denies" in roles:
            fail("", "an equal entry has no denies side; use relation absent for isolation")
        if not PRODUCER_ROLES.intersection(roles) or not CONSUMER_ROLES.intersection(roles):
            fail(
                "", "an equal entry needs a side that defines/grants and one that consumes/presents"
            )
        values = [value for _party, _role, value, _texts in checked]
        if len(set(values)) > 1:
            listed = "; ".join(f"{party}={value!r}" for party, _role, value, _texts in checked)
            hint = " (near-miss: differs only in case or separators)" if near_miss(values) else ""
            fail("", f"MISMATCH: the parties name it differently: {listed}{hint}")
    elif relation == "absent":
        subjects = [item for item in checked if item[1] == "presents"]
        deniers = [item for item in checked if item[1] == "denies"]
        if len(subjects) != 1 or not deniers or len(subjects) + len(deniers) != len(checked):
            fail("", "an absent entry needs exactly one presents side and at least one denies side")
        elif deniers:
            subject = subjects[0][2]
            for party, _role, _value, texts in deniers:
                if any(contains_token(text, subject) for text in texts):
                    fail(party, f"LEAK: {subject!r} appears in the record that must exclude it")
    return findings, notes, label


def verify_register(register: Mapping[str, Any], roots: Roots) -> tuple[list[Finding], list[str]]:
    """Verify a parsed register against its records. Pure apart from reading records."""

    findings: list[Finding] = []
    notes: list[str] = []
    if register.get("schema") != SCHEMA:
        return [Finding("register", "", f"schema must be {SCHEMA!r}")], notes
    entries = register.get("entries")
    if not isinstance(entries, list):
        return [Finding("register", "", "entries must be a list")], notes
    if not entries and _text(register.get("no_boundary_basis")) is None:
        findings.append(
            Finding(
                "register",
                "",
                "no entries and no no_boundary_basis: say why this run crosses no boundary",
            )
        )
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        entry_findings, entry_notes, label = _check_entry(entry, index, roots)
        if label in seen:
            entry_findings.append(Finding(label, "", "duplicate entry id"))
        seen.add(label)
        findings.extend(entry_findings)
        notes.extend(entry_notes)
    return findings, notes


def required(root: pathlib.Path) -> bool:
    """A current-contract run (ignition wrote the agreement contract) must carry a register."""

    path = root / "harness.json"
    if not path.is_file() or path.is_symlink():
        return False
    try:
        document = json.loads(_read_regular(path, ceiling=1024 * 1024))
    except (OSError, ValueError):
        # Unreadable metadata cannot downgrade a run to legacy; treat it as current.
        return True
    return not isinstance(document, Mapping) or "agreement_contract_version" in document


def verify_run(
    root: pathlib.Path,
    *,
    target: pathlib.Path | None,
    candidate: pathlib.Path | None = None,
) -> Report:
    """Verify ``<root>/artifacts/boundary-register.json`` for one run."""

    root = pathlib.Path(root)
    path = root / "artifacts" / REGISTER_NAME
    if not path.exists() and not path.is_symlink():
        if required(root):
            raise BoundaryRegisterError(
                f"no {REGISTER_NAME}: a current run must register every boundary identifier"
            )
        return Report("", 0, (), ("legacy run without a boundary register",))
    try:
        raw = _read_regular(path, ceiling=MAX_REGISTER_BYTES)
        register = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundaryRegisterError(f"{REGISTER_NAME} is unreadable: {exc}") from exc
    if not isinstance(register, Mapping):
        raise BoundaryRegisterError(f"{REGISTER_NAME} must be a JSON object")
    checkout = candidate if candidate is not None else target
    roots = Roots(
        run=root.resolve(),
        target=checkout.resolve() if checkout is not None else None,
        candidate=candidate is not None,
    )
    findings, notes = verify_register(register, roots)
    entries = register.get("entries")
    return Report(
        register_digest="sha256:" + _sha256(raw),
        entries=len(entries) if isinstance(entries, list) else 0,
        findings=tuple(findings),
        notes=tuple(notes),
    )


def cite(
    *,
    kind: str,
    base: pathlib.Path,
    path: str,
    value: str,
    line: int | None = None,
    command: str | None = None,
    root_name: str | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Build a record from the source itself, so nobody types a digest or a line by hand.

    Refuses when the literal is not where the record would claim it is.
    """

    raw = _read_regular(_resolve(base, path), ceiling=MAX_RECORD_BYTES)
    text = raw.decode("utf-8", errors="surrogateescape")
    record: dict[str, Any] = {"kind": kind, "path": path, "sha256": _sha256(raw)}
    if root_name:
        record["root"] = root_name
    if kind == "file":
        lines = text.splitlines()
        if line is None:
            hits = [number for number, body in enumerate(lines, 1) if contains_token(body, value)]
            if not hits:
                raise ValueError(f"{path} does not contain {value!r}")
            line = hits[0]
        if not 1 <= line <= len(lines) or not contains_token(lines[line - 1], value):
            raise ValueError(f"{path}:{line} does not contain {value!r}")
        record["line"] = line
    else:
        if not command or not command.strip():
            raise ValueError("an observation must name the command that produced it")
        if not contains_token(text, value):
            raise ValueError(f"observation {path} does not contain {value!r}")
        moment = now or datetime.datetime.now(datetime.UTC)
        record["command"] = command
        record["observed_at"] = moment.isoformat(timespec="seconds")
    return record


def _print_report(report: Report) -> None:
    for note in report.notes:
        print(f"note: {note}")
    for finding in report.findings:
        print(f"FAIL  {finding.render()}")
    if report.ok:
        digest = f" ({report.register_digest})" if report.register_digest else ""
        noun = "entry" if report.entries == 1 else "entries"
        print(f"boundary register: {report.entries} {noun} verified{digest}")
    else:
        print(
            f"boundary register: {len(report.findings)} finding(s) across {report.entries} entries"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="verify a run's boundary register")
    verify.add_argument("--root", required=True, type=pathlib.Path, help="run control root")
    verify.add_argument("--workdir", type=pathlib.Path, help="target checkout at the run base")
    verify.add_argument(
        "--candidate", type=pathlib.Path, help="candidate checkout; target records re-resolve here"
    )
    record = commands.add_parser("cite", help="print a record built from the source itself")
    record.add_argument("--kind", choices=("file", "observation"), required=True)
    record.add_argument(
        "--base", required=True, type=pathlib.Path, help="directory the path is relative to"
    )
    record.add_argument("--path", required=True)
    record.add_argument("--value", required=True, help="the exact literal")
    record.add_argument("--line", type=int)
    record.add_argument("--command", dest="observed_command")
    record.add_argument("--root-name", choices=sorted(ROOTS))
    args = parser.parse_args(argv)
    if args.command == "cite":
        try:
            built = cite(
                kind=args.kind,
                base=args.base.resolve(),
                path=args.path,
                value=args.value,
                line=args.line,
                command=args.observed_command,
                root_name=args.root_name,
            )
        except (OSError, ValueError) as exc:
            print(f"boundary register: cannot cite: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(built, sort_keys=True))
        return 0
    try:
        report = verify_run(args.root, target=args.workdir, candidate=args.candidate)
    except BoundaryRegisterError as exc:
        print(f"FAIL  {exc}")
        return 1
    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
