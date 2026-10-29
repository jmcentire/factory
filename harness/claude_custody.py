"""Prepare, never activate, an ordered native custody hook for one Claude session.

The existing Signet module can answer classic.PreToolUse before settings hooks run.
Declare it as a dependency so custody evaluates first, retaining its denial before
returning. An allow still traverses Signet and the host. No evaluator or global
configuration is changed. The generated directory must be host-protected and
qualified in the actual CLI before it can establish provider acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex

import signet_hooks


def files(contract: pathlib.Path, digest: str, python: pathlib.Path, *,
          custody_socket: str, custody_digest: str,
          owner_plugin: str = "signet-eval-functions@skills-dir") -> dict[str, str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*@[a-z0-9][a-z0-9_-]*", owner_plugin):
        raise ValueError("an exact existing native policy owner is required")
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("an absolute executable Python interpreter is required")
    settings = signet_hooks.configuration("claude", contract, digest, python,
        custody_socket=custody_socket, custody_digest=custody_digest)
    argv = shlex.split(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
    template = pathlib.Path(__file__).with_suffix(".js").read_text()
    module = template.replace("__FACTORY_CUSTODY_ARGV__", json.dumps(argv))
    manifest = {"name": "factory-policy-custody", "version": "1.0.0",
                "description": "Retain Factory policy holds before native policy short-circuits",
                "author": {"name": "Factory"}, "dependencies": [owner_plugin]}
    return {".claude-plugin/plugin.json": json.dumps(manifest, indent=2) + "\n",
            "hooks/hooks.json": json.dumps({"modules": ["./custody.js"]}) + "\n",
            "hooks/custody.js": module}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=pathlib.Path, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--python", type=pathlib.Path, required=True)
    parser.add_argument("--custody-socket", required=True)
    parser.add_argument("--custody-digest", required=True)
    parser.add_argument("--owner-plugin", default="signet-eval-functions@skills-dir")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    prepared = files(args.contract, args.digest, args.python,
        custody_socket=args.custody_socket, custody_digest=args.custody_digest,
        owner_plugin=args.owner_plugin)
    # No replacement or merge into an installed plugin or existing live hook directory.
    args.output.mkdir(mode=0o700)
    for name, text in prepared.items():
        target = args.output / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("x") as stream:
            stream.write(text)
    print(json.dumps({"prepared": str(args.output), "activated": False,
                      "requires": "host protection, native ordering and live qualification"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
