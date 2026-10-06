"""A stand-in `kindex-lite` MCP server for tests (CI has no Kindex).

Speaks just enough MCP over stdio for lane_kindex's discovery: initialize, tools/list, and
tools/call scope_info. Its tool list and scope come from `stub-config.json` beside the launched
script, so a test can make it misbehave (expose a write tool, allow submissions, name the wrong
repository) without touching the environment the lane server is started with.
"""

from __future__ import annotations

import json
import pathlib
import sys

config = json.loads((pathlib.Path(sys.argv[0]).parent / "stub-config.json").read_text())
repo = sys.argv[sys.argv.index("--repo") + 1]
scope = {"ok": True, "repo": repo, "data_dir": f"{repo}/.kin/local/kindex", "graph": "project",
         "kinbase_submissions_allowed": False, **config.get("scope", {})}

for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        continue
    if method == "initialize":
        result: dict[str, object] = {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "serverInfo": {"name": "kindex-lite", "version": "stub"}}
    elif method == "tools/list":
        result = {"tools": [{"name": name, "inputSchema": {}} for name in config["tools"]]}
    else:
        result = {"content": [{"type": "text", "text": json.dumps(scope)}],
                  "structuredContent": {"result": scope}}
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
