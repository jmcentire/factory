#!/usr/bin/env python3
"""model_profile — the harness side of model profiles (factory_runtime/model_profiles.py).

``factory.sh`` resolves the profile a run uses and snapshots it into the run; ``tmux_lane.sh``
reads one role's binding back from that snapshot. Every command prints NUL-terminated fields,
the form the scripts read with ``read -d ''``.

  resolve    ``none`` when the operator has no profiles and nothing was asked for, else
             ``profile`` then the name, the digest, and each role's agent and model in ROLES
             order. A refusal prints nothing, so a missing first field always means stop.
  snapshot   record how the run was started, refused if that changed since ``resolve``:
             ``--expect-digest none`` records an explicit no-profile snapshot.
  binding    from a run's snapshot: ``none`` when it was started without a profile, else
             ``profile`` then one role's agent, model, and ``metered`` or ``unmetered`` (the
             profile marked it, or its agent bills per use by construction). A missing or
             malformed snapshot is refused, so a lane never guesses how its run began.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from factory_runtime.model_profiles import (  # noqa: E402 - repository root added above
    ROLES,
    Binding,
    ProfileError,
    default_path,
    read_snapshot,
    resolve,
    write_snapshot,
)


def _nul(*fields: str) -> None:
    sys.stdout.write("".join(field + "\0" for field in fields))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="model profiles for the factory harness")
    commands = parser.add_subparsers(dest="command", required=True)
    resolve_cmd = commands.add_parser("resolve", help="the profile a run uses, if any")
    snapshot_cmd = commands.add_parser("snapshot", help="record the resolved profile in a run")
    for sub in (resolve_cmd, snapshot_cmd):
        sub.add_argument("--name", default="")
        sub.add_argument("--profiles", default="")
    snapshot_cmd.add_argument("--expect-digest", required=True)
    snapshot_cmd.add_argument("--output", required=True)
    binding_cmd = commands.add_parser("binding", help="one role's binding from a run snapshot")
    binding_cmd.add_argument("--snapshot", required=True)
    binding_cmd.add_argument("--role", required=True, choices=ROLES)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "binding":
            recorded = read_snapshot(pathlib.Path(arguments.snapshot))
            if recorded is None:
                _nul("none")
                return 0
            bound: Binding = recorded[arguments.role]
            _nul("profile", bound.agent, bound.model,
                 "metered" if bound.is_metered else "unmetered")
            return 0
        path = (
            pathlib.Path(arguments.profiles).expanduser()
            if arguments.profiles
            else default_path()
        )
        profile = resolve(path, arguments.name)
        if arguments.command == "snapshot":
            digest = "none" if profile is None else profile.digest
            if digest != arguments.expect_digest:
                raise ProfileError(f"{path} changed during ignition; ignite again")
            write_snapshot(profile, path, pathlib.Path(arguments.output))
            return 0
        if profile is None:
            _nul("none")
            return 0
        fields = ["profile", profile.name, profile.digest]
        for role in ROLES:
            fields += [profile.roles[role].agent, profile.roles[role].model]
        _nul(*fields)
        return 0
    except (OSError, ProfileError) as error:
        print(f"model profile: {error}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    sys.exit(main())
