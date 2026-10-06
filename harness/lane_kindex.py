#!/usr/bin/env python3
"""lane_kindex — give a Coder or Tester lane a Kindex scoped to its own copy of the target's .kin.

The Validator and the Orchestrator use the operator's full Kindex graph. A Coder or Tester lane
must not: a shared graph is a channel between the lanes (Tester fixtures surfacing to the Coder
retired three Coder seats in ci-r2). Removing Kindex from the lane is also wrong, because the lane
prompts require it and a search-before-explore policy then deadlocks the seat. Scope the DATA,
not the capability.

Each lane's server is ``kindex-lite --repo <lane copy>`` (Kindex >= 0.48.0), which binds to that
copy's ``.kin/local/kindex`` store, refuses global/profile/environment redirection, admits only
an allowlist of memory, task and coordination tools, and by default allows Kinbase sync and
explain but neither submission nor the status call that can write. The lane freeze leaves
``.kin/local`` and ``.kin/events`` out of the judged snapshot.

Before launch the store is loaded, under the lane's private HOME, from approved inputs only:
the copy's own .kin export (the working tree's, which git may not carry), the run's shared seed
(identical for both lanes), and the repository's Kinbase evidence, verified from the copy's
.kin/events in raw mode without the Kinbase binary. A copy that arrives already carrying a
.kin/local store is refused: that is the operator's unreviewed graph, not shared context.

Then the gate. The launch is refused, never downgraded to the full ``kin-mcp`` (which exposes
ingest and exec tools), unless: the installation reports Kindex >= 0.48.0; the lane's own server,
started exactly as the lane will start it, lists kinbase_sync and kinbase_explain and lacks
kinbase_submit and kinbase_status; and its scope_info reports submissions disallowed, this
repository, and a store inside it. The server runs on a PATH holding no ``kinbase`` binary, so
lanes read Kinbase as of launch and live lookups stay off (founder decision 2026-10-06).
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
import threading

SCHEMA = "factory-lane-kindex/2"
MINIMUM_KINDEX = (0, 48, 0)
SERVER_PATH = ("/usr/bin", "/bin")
KNOWLEDGE_EXPORTS = ("knowledge.jsonl", "knowledge.json")
REQUIRED_TOOLS = ("kinbase_sync", "kinbase_explain", "search", "add", "scope_info")
FORBIDDEN_TOOLS = ("kinbase_submit", "kinbase_status")
DISCOVERY_TIMEOUT_S = 60
SEED_TAG = "lane-seed"  # how a lane finds the vital context first, above thousands of others


def _refuse(message: str) -> SystemExit:
    return SystemExit(f"lane-kindex: {message}")


def _tool(name: str, override: str) -> pathlib.Path:
    found = os.environ.get(override) or shutil.which(name)
    if not found:
        raise _refuse(
            f"`{name}` not found. Coder and Tester lanes need Kindex >= 0.48.0 "
            f"(`pip install 'kindex[mcp,kinbase]>=0.48.1'`), or set {override}"
        )
    return pathlib.Path(found).resolve()


def _toml_string(value: str) -> str:
    # A JSON string literal is a valid TOML basic string for every character JSON escapes.
    return json.dumps(value)


def _sha256(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _tagged_seed(seed: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    """Copy the seed with every node tagged SEED_TAG, so `search(tags=...)` surfaces it first."""

    lines = []
    for number, line in enumerate(seed.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            node = json.loads(line)
        except ValueError as exc:
            raise _refuse(f"seed line {number} is not JSON: {exc}") from exc
        if not isinstance(node, dict) or not node.get("title"):
            raise _refuse(f"seed line {number} is not a node with a title")
        # `kin import` reads tags from `domains` (a list); exports may carry a `tags` string.
        merged = []
        for key in ("domains", "tags"):
            value = node.get(key) or []
            merged += [t.strip() for t in (value.split(",") if isinstance(value, str) else value)
                       if isinstance(t, str) and t.strip()]
        node["domains"] = list(dict.fromkeys([*merged, SEED_TAG]))
        lines.append(json.dumps(node, sort_keys=True))
    destination.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return destination


def _version(kin: pathlib.Path) -> str:
    done = subprocess.run([str(kin), "--version"], capture_output=True, text=True, check=False)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", done.stdout)
    if done.returncode != 0 or not match:
        raise _refuse(f"cannot read the Kindex version from {kin}")
    if tuple(int(part) for part in match.groups()) < MINIMUM_KINDEX:
        raise _refuse(f"Kindex {match.group(0)} predates kindex-lite's read-only Kinbase default; "
                      "install >= 0.48.1")
    return match.group(0)


def _discover(
    command: list[str], environment: dict[str, str], repo: pathlib.Path
) -> dict[str, object]:
    """Start the lane's server exactly as the lane will; return its tools and scope.

    MCP over stdio is a conversation: the server stops when its input closes, so each request
    waits for its reply before the next is sent, under one deadline that kills a hung server.
    """

    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=environment, cwd=repo, text=True)
    timer = threading.Timer(DISCOVERY_TIMEOUT_S, process.kill)
    timer.start()
    assert process.stdin is not None and process.stdout is not None

    def send(message: dict[str, object]) -> None:
        process.stdin.write(json.dumps(message) + "\n")  # type: ignore[union-attr]
        process.stdin.flush()  # type: ignore[union-attr]

    def reply(wanted: int) -> dict[str, object]:
        for line in process.stdout:  # type: ignore[union-attr]
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == wanted:
                return message
        raise _refuse("the lane's Kindex server stopped before answering discovery: "
                      f"{process.stderr.read().strip()[-300:]}")  # type: ignore[union-attr]

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "factory-lane-kindex", "version": "1"}}})
        reply(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = reply(2)
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "scope_info", "arguments": {}}})
        called = reply(3)
    finally:
        timer.cancel()
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    try:
        tools = sorted(tool["name"] for tool in listed["result"]["tools"])  # type: ignore[index]
        result = called["result"]
        assert isinstance(result, dict)
        scope = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        scope = scope.get("result", scope)
    except (KeyError, IndexError, TypeError, ValueError, AssertionError) as exc:
        raise _refuse(f"the lane's Kindex server gave no usable discovery answer: {exc}") from exc
    return {"tools": tools, "scope": scope}


def _gate(discovered: dict[str, object], repo: pathlib.Path) -> None:
    tools = set(discovered["tools"])  # type: ignore[call-overload]
    scope = discovered["scope"]
    assert isinstance(scope, dict)
    missing = [name for name in REQUIRED_TOOLS if name not in tools]
    exposed = [name for name in FORBIDDEN_TOOLS if name in tools]
    if missing or exposed:
        raise _refuse(f"lane server failed its permission check (missing {missing}, "
                      f"write tools exposed {exposed})")
    data_dir = pathlib.Path(str(scope.get("data_dir", ""))).resolve()
    if (scope.get("ok") is not True or scope.get("kinbase_submissions_allowed") is not False
            or pathlib.Path(str(scope.get("repo", ""))).resolve() != repo
            or repo / ".kin" / "local" not in (data_dir, *data_dir.parents)):
        raise _refuse(f"lane server's scope_info does not match this lane: {scope}")


def prepare(
    repo: pathlib.Path, home: pathlib.Path, seed: pathlib.Path | None = None
) -> dict[str, object]:
    repo = repo.resolve()
    if not repo.is_dir():
        raise _refuse(f"lane repository is not a directory: {repo}")
    lite = _tool("kindex-lite", "FACTORY_KINDEX_LITE_BIN")
    kin = lite.parent / "kin"  # the same installation's CLI, never another one on PATH
    if not os.access(kin, os.X_OK):
        raise _refuse(f"no `kin` beside {lite}; reinstall Kindex")
    version = _version(kin)

    # A relaunch of the same lane keeps its own store; a home bound to another repository is
    # another lane's memory and is never shared. The binding is written only after a launch
    # fully succeeds, so a home without it is an interrupted attempt, never a lane to resume.
    marker = home / ".factory-lane-kindex"
    local = repo / ".kin" / "local"
    relaunch = False
    if home.exists() and any(home.iterdir()):
        bound = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        if not bound:
            raise _refuse(f"{home} holds an interrupted launch; remove it and {local}, then "
                          "relaunch")
        if bound != str(repo):
            raise _refuse(f"{home} belongs to another lane ({bound})")
        relaunch = True
    if not relaunch and (local.is_symlink() or (local.exists() and any(local.iterdir()))):
        raise _refuse(f"{local} already holds a store; make the lane copy without .kin/local so "
                      "the lane starts from the shared inputs only")
    server_path = os.pathsep.join(SERVER_PATH)
    if shutil.which("kinbase", path=server_path):
        raise _refuse(f"`kinbase` is on the lane server's PATH ({server_path}); live Kinbase "
                      "lookups stay off for lanes, which read it from their copy's .kin/events")
    if seed is not None and (seed.is_symlink() or not seed.is_file()):
        raise _refuse(f"seed must be a regular file: {seed}")

    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    home = home.resolve()
    environment = {"HOME": str(home), "KIN_PROJECT_PATH": str(repo), "PATH": server_path}

    def load(argv: list[str], what: pathlib.Path) -> str:
        done = subprocess.run([str(kin), *argv], env=environment, cwd=repo,
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise _refuse(f"loading {what} failed: {done.stderr.strip()}")
        return done.stdout

    def created(out: str) -> int:
        match = re.search(r"(\d+) created", out)
        return int(match.group(1)) if match else 0

    source = next((repo / ".kin" / name for name in KNOWLEDGE_EXPORTS
                   if (repo / ".kin" / name).is_file()), None)
    imported = 0
    kinbase = None
    seeded = None if seed is None else {"path": str(seed.resolve()), "digest": _sha256(seed),
                                        "imported": 0}
    command = [str(lite), "--repo", str(repo)]
    try:
        if not relaunch:  # a relaunched lane keeps the store it built and its own captures
            if source is not None:
                imported = created(load(["import", str(source), "--project-path", str(repo)],
                                        source))
            # The run's shared seed: the vital context both lanes may read (research, standing
            # constraints, ratified decisions), the same file for both. Never implementation or
            # tests.
            if seed is not None and seeded is not None:
                tagged = _tagged_seed(seed, home / "lane-seed.tagged.jsonl")
                seeded["imported"] = created(
                    load(["import", str(tagged), "--project-path", str(repo)], seed)
                )
                seeded["tag"] = SEED_TAG
            events = repo / ".kin" / "events"
            if events.is_dir() and not events.is_symlink():
                synced = json.loads(load(["kinbase", "sync", "--repo", str(repo),
                                          "--project-path", str(repo), "--mode", "raw",
                                          "--json"], events))
                kinbase = {"events": str(events), "imported": synced.get("imported", 0),
                           "quarantined": synced.get("quarantined", 0),
                           "reads": "frozen-at-launch", "writes": "unavailable"}
        discovered = _discover(command, environment, repo)
        _gate(discovered, repo)
    except BaseException:
        if not relaunch:  # undo only what this attempt created; both were empty when it began
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(local, ignore_errors=True)
        raise
    marker.write_text(str(repo) + "\n", encoding="utf-8")
    scope = discovered["scope"]
    assert isinstance(scope, dict)

    env_table = ",".join(f"{key}={_toml_string(value)}" for key, value in environment.items())
    args = ",".join(_toml_string(part) for part in command[1:])
    override = (f"mcp_servers.kindex={{command={_toml_string(command[0])},args=[{args}],"
                f"env={{{env_table}}}}}")
    return {
        "schema_version": SCHEMA,
        "scope": "lane-copy-of-target-kin",
        "server": "kindex-lite",
        "kindex_version": version,
        "repository": str(repo),
        "home": str(home),
        "store": str(scope.get("data_dir")),
        "tools": discovered["tools"],
        "kinbase_submissions_allowed": False,
        "relaunch": relaunch,
        "source": str(source) if source is not None else None,
        "source_digest": _sha256(source) if source is not None else None,
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
    prep.add_argument("--home", required=True, help="the lane's private Kindex home")
    prep.add_argument("--seed", help="the run's shared seed (node JSONL), same for both lanes")
    arguments = parser.parse_args(argv)
    record = prepare(pathlib.Path(arguments.repo), pathlib.Path(arguments.home),
                     pathlib.Path(arguments.seed) if arguments.seed else None)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
