"""Resolve role choices without approving models or substituting after failures.

User arguments/environment override local defaults, which override shipped defaults.
An empty model delegates to the selected CLI's configuration and built-in default.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

ROLES = ("validator", "orchestrator", "coder", "tester")
FIELDS = ("agent", "model", "effort")
DEFAULTS = {
    role: {"agent": "agy" if role == "orchestrator" else "codex", "model": "", "effort": ""}
    for role in ROLES
}


def resolve(
    role: str,
    *,
    explicit: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ if environ is None else environ
    chosen = dict(DEFAULTS[role])
    path = Path(
        env.get("FACTORY_CONFIG", str(Path(env.get("HOME", "~")) / ".config/factory/config.toml"))
    )
    if path.exists() or "FACTORY_CONFIG" in env:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
        unknown = config.keys() - {"roles"}
        roles = config.get("roles", {})
        if unknown or not isinstance(roles, dict) or roles.keys() - set(ROLES):
            raise ValueError(f"invalid role defaults in {path}")
        for name, settings in roles.items():
            if not isinstance(settings, dict) or settings.keys() - set(FIELDS):
                raise ValueError(f"invalid settings for {name} in {path}")
            if any(not isinstance(v, str) or "\0" in v for v in settings.values()):
                raise ValueError(f"role choices must be literal strings in {path}")
        chosen.update(roles.get(role, {}))
    overrides = {
        field: env[f"FACTORY_{role.upper()}_{field.upper()}"]
        for field in FIELDS
        if f"FACTORY_{role.upper()}_{field.upper()}" in env
    }
    overrides.update(explicit or {})
    # A model default for one CLI must not accidentally select a model on another.
    if "agent" in overrides and overrides["agent"] != chosen["agent"]:
        chosen.update(model="", effort="")
    chosen.update(overrides)
    if not chosen["agent"] or any("\0" in value for value in chosen.values()):
        raise ValueError("role choices must name an agent and contain no NUL bytes")
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=ROLES)
    for field in FIELDS:
        parser.add_argument(f"--{field}")
    args = parser.parse_args()
    try:
        settings = resolve(
            args.role,
            explicit={
                field: vars(args)[field] for field in FIELDS if vars(args)[field] is not None
            },
        )
        for field in FIELDS:
            sys.stdout.buffer.write(settings[field].encode() + b"\0")
    except (OSError, ValueError) as error:
        print(f"model-settings: {error}", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
