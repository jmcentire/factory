#!/usr/bin/env python3
"""Snapshot the target repository's own rule files into the run record.

The invariant this module upholds: **the rules the target enforces on its own PRs are part of
the run, read from the pinned tree, not remembered.** A target's working agreements
(``AGENTS.md``, ``CLAUDE.md``, ``CONTRIBUTING.md``, ``ARCHITECTURE.md``) and its machine-
reviewed rule sets (``.claude/rules/**``, ``.rule-review/rules/**``) bind every change a run
delivers, yet the lane projection freeze-excludes the agent-guidance files, so the Coder and
Tester see a rule only when the Validator quotes it. Runs that never read them delivered PRs
that broke them: one invented a default the source never gave, one turned a failed lookup into
a silent fallback, and each cost a review round.

``snapshot`` copies whichever of those files exist in the target workdir into
``<artifacts>/repo-rules/`` with a ``manifest.json`` binding each copy's SHA-256 and, for a
Markdown file opening with front matter, its ``id`` and ``title``. Re-running against the same
bytes is a no-op; a different rule set refuses, because the rules are frozen for the run.

Finding the files is mechanical. Deciding which rule a requirement touches is judgment, and
stays with the Validator (``prompts/validate.md``, Repo rules).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import sys
from collections.abc import Sequence
from typing import Any

SCHEMA = "repo-rules/1"
DIRNAME = "repo-rules"
MANIFEST = "manifest.json"
# Working-agreement files are read at any depth: a monorepo keeps one per service.
NAMED = frozenset({"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "ARCHITECTURE.md"})
# Rule trees are read whole, from the target root.
TREES = (".claude/rules", ".rule-review/rules")
# ponytail: fixed prune list; read the target's freeze-exclude lines if a vendored tree carries
# a working-agreement file that is not the target's own.
PRUNE = frozenset({".git", "node_modules"})
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FIELD = re.compile(r"^(id|title):[ \t]*(.*?)[ \t]*$", re.MULTILINE)


class RepoRulesError(Exception):
    """The snapshot cannot be taken or would change a frozen one."""


def rule_paths(workdir: pathlib.Path) -> list[str]:
    """Every rule source in the tree, as sorted POSIX paths relative to ``workdir``."""

    found: set[str] = set()
    for path in workdir.rglob("*"):
        rel = path.relative_to(workdir)
        if PRUNE.intersection(rel.parts):
            continue
        posix = rel.as_posix()
        if path.name in NAMED or any(posix.startswith(tree + "/") for tree in TREES):
            if path.is_symlink() or path.is_file():
                found.add(posix)
    return sorted(found)


def front_matter(text: str) -> dict[str, str]:
    """The ``id`` and ``title`` of a leading YAML front-matter block, unquoted."""

    block = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if not block:
        return {}
    return {key: value.strip("'\"") for key, value in _FIELD.findall(block.group(1))}


def snapshot(workdir: pathlib.Path, artifacts: pathlib.Path) -> dict[str, Any]:
    workdir = workdir.resolve()
    if not workdir.is_dir():
        raise RepoRulesError(f"target workdir is not a directory: {workdir}")
    entries: list[dict[str, Any]] = []
    for rel in rule_paths(workdir):
        source = workdir / rel
        if source.is_symlink() or not source.resolve().is_relative_to(workdir):
            # Never follow a link out of the pinned tree; record where it points instead.
            entries.append({"path": rel, "symlink": str(source.resolve())})
            continue
        data = source.read_bytes()
        entry: dict[str, Any] = {
            "path": rel,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        if rel.endswith(".md"):
            entry.update(front_matter(data.decode("utf-8", errors="replace")))
        entries.append(entry)
    manifest = {"schema": SCHEMA, "files": entries}
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    out = artifacts / DIRNAME
    existing = out / MANIFEST
    if existing.exists():
        if existing.read_text(encoding="utf-8") != rendered:
            raise RepoRulesError(
                f"{existing} records a different rule set; the rules are frozen for the run"
            )
        return manifest
    if out.exists():
        raise RepoRulesError(f"{out} exists without a manifest; refusing to mix snapshots")
    for entry in entries:
        if "sha256" in entry:
            target = out / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(workdir / entry["path"], target)
    out.mkdir(parents=True, exist_ok=True)
    existing.write_text(rendered, encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="copy the target's rule files into the run")
    snap.add_argument(
        "--workdir", required=True, type=pathlib.Path, help="target checkout at the run base"
    )
    snap.add_argument("--artifacts", required=True, type=pathlib.Path, help="run artifacts dir")
    args = parser.parse_args(argv)
    try:
        manifest = snapshot(args.workdir, args.artifacts)
    except (RepoRulesError, OSError) as exc:
        print(f"repo_rules: {exc}", file=sys.stderr)
        return 1
    for entry in manifest["files"]:
        label = entry.get("id") or entry.get("symlink") or ""
        print(f"{entry['path']}\t{label}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
