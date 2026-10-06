#!/usr/bin/env python3
"""lane_kindex — give a Coder or Tester lane a Kindex scoped to its own copy of the target's .kin.

The Validator and the Orchestrator use the operator's full Kindex graph. A Coder or Tester lane
must not: a shared graph is a channel between the lanes (Tester fixtures surfacing to the Coder
retired three Coder seats in ci-r2). Removing Kindex from the lane is also wrong, because the lane
prompts require it and a search-before-explore policy then deadlocks the seat. The fix that run
reached, and this module makes standard, is to scope the DATA, not remove the capability.

For each lane this prepares a private Kindex home under the run's control root:

  <home>/.config/kindex/kin.yaml   a single profile whose data lives at <home>/store
  <home>/store                     loaded from the lane repository's committed .kin/knowledge.jsonl

then loads, in order: the copy's own .kin export (the working tree's, which git may not carry);
the run's shared seed, when the Validator wrote one, identical for both lanes; and the company's
Kinbase evidence for the repository, verified from the copy's .kin/events without the Kinbase
binary. It prints the Codex ``-c`` override that starts ``kin-mcp`` with HOME=<home> and that
profile, on a PATH holding no ``kinbase``, so lanes read Kinbase but can never write to it.
With a private HOME the operator's global config is invisible, so there is no outer graph; with
an explicit profile Kindex keeps a single-graph boundary. The store sits outside the lane
repository, so the lane's frozen export never carries a database. Each lane has its own repository
copy and its own home, so nothing one lane captures can reach the other.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

PROFILE = "factory-lane"
SCHEMA = "factory-lane-kindex/1"
# The lane's Kindex server finds `kinbase` on PATH, and every Kinbase write (submit, and the
# status call that can close overdue apologies) runs through that binary. Lanes READ Kinbase from
# the signed events in their own copy, loaded at launch without the binary, so the server's PATH
# must not hold it. Nothing reaches Kinbase until the run's work is done and verified.
SERVER_PATH = ("/usr/bin", "/bin")
KNOWLEDGE_EXPORTS = ("knowledge.jsonl", "knowledge.json")


def _tool(name: str, override: str) -> pathlib.Path:
    found = os.environ.get(override) or shutil.which(name)
    if not found:
        raise SystemExit(
            f"lane-kindex: `{name}` not found. Coder and Tester lanes need Kindex (their prompts "
            f"search and capture there); install it, or set {override}"
        )
    return pathlib.Path(found).resolve()


def _toml_string(value: str) -> str:
    # A JSON string literal is a valid TOML basic string for every character JSON escapes.
    return json.dumps(value)


def prepare(
    repo: pathlib.Path, home: pathlib.Path, seed: pathlib.Path | None = None
) -> dict[str, object]:
    repo = repo.resolve()
    if not repo.is_dir():
        raise SystemExit(f"lane-kindex: lane repository is not a directory: {repo}")
    kin = _tool("kin", "FACTORY_KIN_BIN")
    kin_mcp = _tool("kin-mcp", "FACTORY_KIN_MCP_BIN")

    # A relaunch of the same lane keeps its own store; a home bound to another repository is
    # another lane's memory and is never shared.
    marker = home / ".factory-lane-kindex"
    if home.exists() and any(home.iterdir()):
        bound = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        if bound != str(repo):
            raise SystemExit(f"lane-kindex: {home} belongs to another lane ({bound or 'unknown'})")
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    home = home.resolve()
    marker = home / ".factory-lane-kindex"
    marker.write_text(str(repo) + "\n", encoding="utf-8")
    store = home / "store"
    config = home / ".config" / "kindex" / "kin.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"profiles:\n  {PROFILE}:\n    data_dir: {_toml_string(str(store))}\n", encoding="utf-8"
    )
    server_path = os.pathsep.join(SERVER_PATH)
    if shutil.which("kinbase", path=server_path):
        raise SystemExit(
            f"lane-kindex: `kinbase` is on the lane server's PATH ({server_path}); a lane could "
            "write to Kinbase through it. Lanes read Kinbase from their copy's .kin/events instead"
        )
    environment = {
        "HOME": str(home),
        "KIN_PROFILE": PROFILE,
        "KIN_PROJECT_PATH": str(repo),
        "PATH": server_path,
    }

    def load(argv: list[str], what: pathlib.Path) -> str:
        done = subprocess.run([str(kin), *argv], env=environment, cwd=repo,
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise SystemExit(f"lane-kindex: loading {what} failed: {done.stderr.strip()}")
        return done.stdout

    # The working tree's .kin, not git's: many repositories keep .kin out of version control, so
    # a lane copy must be made from the working tree to carry it.
    source = next((repo / ".kin" / name for name in KNOWLEDGE_EXPORTS
                   if (repo / ".kin" / name).is_file()), None)
    source_digest = None
    imported = 0
    if source is not None:
        source_digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        out = load(["import", str(source), "--project-path", str(repo)], source)
        match = re.search(r"(\d+) created", out)
        imported = int(match.group(1)) if match else 0

    # The run's shared seed: the vital context both lanes may read (research, standing
    # constraints, ratified decisions), the same file for both. Never implementation or tests.
    seeded = None
    if seed is not None:
        if seed.is_symlink() or not seed.is_file():
            raise SystemExit(f"lane-kindex: seed must be a regular file: {seed}")
        out = load(["import", str(seed.resolve()), "--project-path", str(repo)], seed)
        match = re.search(r"(\d+) created", out)
        seeded = {"path": str(seed.resolve()),
                  "digest": "sha256:" + hashlib.sha256(seed.read_bytes()).hexdigest(),
                  "imported": int(match.group(1)) if match else 0}

    events = repo / ".kin" / "events"
    kinbase = None
    if events.is_dir() and not events.is_symlink():
        out = load(["kinbase", "sync", "--repo", str(repo), "--project-path", str(repo),
                    "--mode", "raw", "--json"], events)
        synced = json.loads(out)
        kinbase = {"events": str(events), "imported": synced.get("imported", 0),
                   "quarantined": synced.get("quarantined", 0), "writes": "unreachable"}

    env_table = ",".join(f"{key}={_toml_string(value)}" for key, value in environment.items())
    override = (
        f"mcp_servers.kindex={{command={_toml_string(str(kin_mcp))},args=[],env={{{env_table}}}}}"
    )
    return {
        "schema_version": SCHEMA,
        "scope": "lane-copy-of-target-kin",
        "repository": str(repo),
        "home": str(home),
        "profile": PROFILE,
        "command": str(kin_mcp),
        "source": str(source) if source_digest else None,
        "source_digest": source_digest,
        "imported_nodes": imported,
        "seed": seeded,
        "kinbase": kinbase,
        "mcp_override": override,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="create a lane's scoped Kindex; print its record")
    prep.add_argument("--repo", required=True, help="the lane's own repository copy")
    prep.add_argument("--home", required=True, help="the lane's private Kindex home (new)")
    prep.add_argument("--seed", help="the run's shared seed (node JSONL), same for both lanes")
    arguments = parser.parse_args(argv)
    record = prepare(pathlib.Path(arguments.repo), pathlib.Path(arguments.home),
                     pathlib.Path(arguments.seed) if arguments.seed else None)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
