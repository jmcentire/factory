#!/usr/bin/env python3
"""CLI entrypoint for validating and freezing an agent-owned lane repository."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from factory_runtime.lane_repository import (
    LaneExport,
    LaneRepositoryError,
    StandaloneRepository,
    freeze_lane_repository,
    validate_standalone_repository,
)


def _standalone_json(repository: StandaloneRepository) -> dict[str, Any]:
    return {
        "schema_version": "factory-standalone-lane-repository/1",
        "root": str(repository.root),
        "git_directory": str(repository.git_directory),
        "common_directory": str(repository.common_directory),
        "ownership_after_launch": "agent-owned-radioactive-no-host-git",
    }


def _export_json(exported: LaneExport) -> dict[str, Any]:
    return {
        "schema_version": "factory-lane-plain-export/1",
        "tree_digest": exported.frozen_tree.digest,
        "snapshot_directory": str(exported.frozen_tree.directory),
        "files_directory": str(exported.frozen_tree.files_directory),
        "source_file_count": exported.source_file_count,
        "source_bytes": exported.source_bytes,
        "excluded_entries": list(exported.excluded_entries),
        "git_invoked_after_handoff": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--source", type=pathlib.Path, required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--source", type=pathlib.Path, required=True)
    freeze.add_argument("--store", type=pathlib.Path, required=True)
    freeze.add_argument("--durable-through", type=pathlib.Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "validate":
            output = _standalone_json(validate_standalone_repository(arguments.source))
        else:
            output = _export_json(
                freeze_lane_repository(
                    arguments.source,
                    arguments.store,
                    durable_through=arguments.durable_through,
                )
            )
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    except (LaneRepositoryError, OSError) as exc:
        print(f"lane repository refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
