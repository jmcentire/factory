"""Read a run's declared projection limits from the manifest its target resolution retained.

A target declares its own size in ``build.projection_limits``; the factory's defaults fit a small
repository and nothing else.  The limits are read from the exact manifest bytes the run authorized
(``evidence/target-resolution/target-manifest.toml``), never from the live target, and those bytes
must still carry the digest the run's target state names.  A limit the target leaves undeclared
keeps each caller's own default, so a lane freeze and a qualified-lane bundle stay bounded
independently.
"""

from __future__ import annotations

from pathlib import Path

from factory_core.target import TargetManifestError, load_target_manifest

LIMIT_FIELDS = ("max_files", "max_file_bytes", "max_total_bytes")
RETAINED_MANIFEST = Path("evidence") / "target-resolution" / "target-manifest.toml"


class ProjectionLimitsError(ValueError):
    """The retained manifest could not be read as the run's authorized target."""


def retained_projection_limits(control_root: str | Path, *, expected_digest: str) -> dict[str, int]:
    """Return only the limits the run's authorized manifest declares, keyed by field name."""

    path = Path(control_root) / RETAINED_MANIFEST
    if path.is_symlink() or not path.is_file():
        raise ProjectionLimitsError(f"run has no retained target manifest: {path}")
    try:
        manifest = load_target_manifest(path)
    except TargetManifestError as exc:
        raise ProjectionLimitsError(str(exc)) from exc
    if manifest.source_digest != expected_digest:
        raise ProjectionLimitsError(
            "retained target manifest differs from the one the run's target state names"
        )
    declared = manifest.build.get("projection_limits", {})
    return {field: int(declared[field]) for field in LIMIT_FIELDS if field in declared}


__all__ = [
    "LIMIT_FIELDS",
    "ProjectionLimitsError",
    "RETAINED_MANIFEST",
    "retained_projection_limits",
]
