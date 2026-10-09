#!/usr/bin/env python3
"""testing_standard — the founder's testing standard, injected into every Tester lane's prompt.

The invariant: **a Tester lane starts with the testing standard in its prompt, or it does not
start.** In msg-r2 the Tester briefs pointed at the contracts, never at ``TESTING.md``; the
Tester never opened it and repeated the setup faults the standard forbids
(docs/practices/lessons-msg-r2-2026-10.md §6). A path is a suggestion a model can skip, so the
lane receives the bytes themselves, each fenced with its sha256 so the retained prompt proves
what the lane was given:

- ``docs/standards/TESTING.md`` — How We Test (the T-rules);
- ``prompts/test.md`` — the Tester role doctrine;
- the run's ``testing-strategy.md``, when the caller passes one (``tmux_lane.sh`` passes the
  run's; ``dispatch_lane.sh`` does not, because run-model injects the ratified strategy from
  the state capsule and a mutable Markdown view must not condition a qualified lane, Gate B).

A source that is missing, empty, a symlink, not a regular file or not UTF-8 refuses the block,
and the caller refuses the launch.

  block   write the block to ``--output`` and print its sources' digests as JSON.

Stdlib only, and no imports from this package: the shell entry points call it by path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile

SCHEMA = "factory-testing-standard-injection/1"
MAX_SOURCE_BYTES = 1024 * 1024
STANDARD = pathlib.PurePosixPath("docs/standards/TESTING.md")
DOCTRINE = pathlib.PurePosixPath("prompts/test.md")
MARKER = "FACTORY_TESTING_STANDARD"

PREAMBLE = """MANDATORY FIRST READING — THE FOUNDER'S TESTING STANDARD (role=tester)
Read every document below in full before you write, change or run any test. They are the
founder's testing standard (How We Test, the T-rules), the Tester role doctrine and, when
present, this run's testing strategy. They bind every test you write: a test that breaks them
is rejected whether or not it passes. Every lane report cites the T-rules it relied on.
"""


class StandardMissing(RuntimeError):
    pass


def _read(path: pathlib.Path, label: str) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise StandardMissing(f"{label} cannot be read at {path}: {error.strerror}") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_SOURCE_BYTES:
            raise StandardMissing(f"{label} at {path} is not a non-empty bounded regular file")
        raw = b""
        while chunk := os.read(descriptor, 1024 * 1024):
            raw += chunk
    finally:
        os.close(descriptor)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StandardMissing(f"{label} at {path} is not UTF-8") from None


def sources(factory_home: pathlib.Path,
            strategy: pathlib.Path | None) -> list[tuple[str, str, pathlib.Path]]:
    named: list[tuple[str, str, pathlib.Path]] = [
        ("testing-standard", "How We Test", factory_home / STANDARD),
        ("tester-doctrine", "Tester role doctrine", factory_home / DOCTRINE),
    ]
    if strategy is not None:
        named.append(("testing-strategy", "this run's testing strategy", strategy))
    return named


def build(factory_home: pathlib.Path,
          strategy: pathlib.Path | None = None) -> tuple[str, dict[str, object]]:
    """The block text and its receipt: each source's path and sha256."""

    parts = [PREAMBLE]
    recorded: dict[str, dict[str, str]] = {}
    for key, label, path in sources(factory_home, strategy):
        if path.is_symlink():
            raise StandardMissing(f"{label} at {path} is a symlink")
        text = _read(path, label)
        digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        recorded[key] = {"path": str(path), "digest": digest}
        parts.append(
            f"\n<<<{MARKER} {key} digest={digest}>>>\n{text.rstrip(chr(10))}\n"
            f"<<<END {MARKER} {key} digest={digest}>>>\n"
        )
    return "".join(parts), {"schema_version": SCHEMA, "sources": recorded}


def write_block(output: pathlib.Path, text: str) -> None:
    if output.is_symlink():
        raise StandardMissing(f"refusing a symlinked block address {output}")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix=".testing-standard-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    except BaseException:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inject the testing standard into a Tester lane")
    commands = parser.add_subparsers(dest="command", required=True)
    block = commands.add_parser("block")
    block.add_argument("--factory-home", type=pathlib.Path, required=True)
    block.add_argument("--strategy", type=pathlib.Path)
    block.add_argument("--output", type=pathlib.Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        text, receipt = build(arguments.factory_home, arguments.strategy)
        write_block(arguments.output, text)
    except (StandardMissing, OSError) as error:
        print(
            f"testing-standard: {error}; a Tester lane does not start without it",
            file=sys.stderr,
        )
        return 70
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
