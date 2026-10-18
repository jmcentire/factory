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
    read_freeze_excludes,
    validate_standalone_repository,
)
from factory_runtime.projection_limits import ProjectionLimitsError, retained_projection_limits


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


def _declared_limits(arguments: argparse.Namespace) -> dict[str, int]:
    """The run's declared projection limits, or none when the caller names no run."""

    if (arguments.control_root is None) != (arguments.target_manifest_digest is None):
        raise LaneRepositoryError(
            "--control-root and --target-manifest-digest must be given together"
        )
    if arguments.control_root is None:
        return {}
    return retained_projection_limits(
        arguments.control_root, expected_digest=arguments.target_manifest_digest
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--source", type=pathlib.Path, required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--source", type=pathlib.Path, required=True)
    freeze.add_argument("--store", type=pathlib.Path, required=True)
    freeze.add_argument("--projection-conf", type=pathlib.Path)
    freeze.add_argument("--durable-through", type=pathlib.Path, required=True)
    freeze.add_argument("--control-root", type=pathlib.Path)
    freeze.add_argument("--target-manifest-digest")
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
                    **_declared_limits(arguments),
                    exclude=(
                        read_freeze_excludes(arguments.projection_conf)
                        if arguments.projection_conf
                        else ()
                    ),
                )
            )
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    except (LaneRepositoryError, ProjectionLimitsError, OSError) as exc:
        print(f"lane repository refused: {exc}", file=sys.stderr)
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
