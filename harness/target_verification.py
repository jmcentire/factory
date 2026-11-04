"""Execute only retained target verification argv and retain its exact endgame subject.

Target tests run in the fresh candidate's authorized subtree. The separate host isolation
proof checks Factory's backend, not the actual run's author-lane qualification or receipts.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

FACTORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FACTORY_ROOT))

from factory_core.manifest import digest_bytes, digest_obj  # noqa: E402
from factory_core.target import load_target_manifest_bytes  # noqa: E402
from factory_runtime.state_admission import read_stable_regular_bytes  # noqa: E402


def _read(path: Path, label: str) -> bytes:
    return read_stable_regular_bytes(path, label=label, max_bytes=16 * 1024 * 1024)


def _no_links(path: Path) -> None:
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            raise ValueError(f"linked verification path is forbidden: {part}")


_TARGET_EXECUTION_CONTROLS = (
    "MAKEFLAGS",
    "MFLAGS",
    "GNUMAKEFLAGS",
    "MAKEFILES",
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "BASH_ENV",
    "ENV",
)


def _binary(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    _no_links(resolved)
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise ValueError("host proof executable is not a regular executable")
    return {
        "path": str(resolved),
        "digest": digest_bytes(_read(resolved, "host executable")),
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _receipt_environment(root: Path) -> dict[str, str]:
    return {
        "HARNESS_DIR": str(root.parent.parent),
        "HARNESS_RUN_ROOT": str(root),
        "HARNESS_BASE_SHA": "",
    }


def _host_environment(root: Path) -> dict[str, str]:
    # This is deliberately a closed host environment, not a target environment template.
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": tempfile.gettempdir(),
        "PYTHONPATH": str(FACTORY_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **_receipt_environment(root),
    }


def _trusted_make(environment: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    selected = shutil.which("make", path=os.defpath)
    if selected is None:
        raise ValueError("host isolation proof has no make in the trusted system tool path")
    selector = None
    if platform.system() == "Darwin":
        selector = _binary(Path("/usr/bin/xcrun"))
        found = subprocess.run(
            [selector["path"], "--find", "make"],
            env=environment,
            cwd=FACTORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        selected = found.stdout.strip()
        if found.returncode or not selected or not Path(selected).is_absolute():
            raise ValueError("trusted host tool selector could not resolve make")
    return _binary(Path(selected)), selector


def _host_identity(root: Path) -> dict[str, Any]:
    paths = [
        FACTORY_ROOT / "Makefile",
        FACTORY_ROOT / "harness" / "endgame.sh",
        Path(__file__),
        FACTORY_ROOT / "harness" / "factory_python.sh",
        FACTORY_ROOT / "harness" / "receipt.sh",
        FACTORY_ROOT / "pyproject.toml",
        FACTORY_ROOT / "tests" / "conftest.py",
        FACTORY_ROOT / "tests" / "test_isolated_build_loop.py",
    ]
    optional_configuration = {}
    for name in ("conftest.py", "pytest.ini", ".pytest.ini", "setup.cfg", "tox.ini"):
        configuration = FACTORY_ROOT / name
        _no_links(configuration)
        optional_configuration[name] = configuration.exists()
        if configuration.exists():
            paths.append(configuration)
    for package in ("factory_core", "factory_runtime", "tests/fixtures/runtime_agents"):
        paths.extend(
            path
            for path in sorted((FACTORY_ROOT / package).rglob("*"))
            if path.suffix in {".py", ".json"} and "__pycache__" not in path.parts
        )
    listing = []
    for path in sorted(set(paths)):
        _no_links(path)
        listing.append(
            {
                "path": path.relative_to(FACTORY_ROOT).as_posix(),
                "digest": digest_bytes(_read(path, "host isolation source")),
            }
        )
    environment = _host_environment(root)
    make, selector = _trusted_make(environment)
    python, shell = _binary(Path(sys.executable)), _binary(Path("/bin/bash"))
    return {
        "factory_root": str(FACTORY_ROOT),
        "source_files": listing,
        "source_listing_digest": digest_obj(listing),
        "optional_configuration_presence": optional_configuration,
        "python": python,
        "make": make,
        "tool_selector": selector,
        "receipt_interpreter": shell,
        "argv": [make["path"], "-C", str(FACTORY_ROOT), f"PY={sys.executable}", "test-isolation"],
        "environment": environment,
        "scope": "host backend proof; current-run lane qualification remains separate",
    }


def _argv(value: Any, label: str, *, optional: bool = False) -> list[str]:
    if optional and value is None:
        return []
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in value)
    ):
        raise ValueError(f"retained manifest has no valid {label} argv; settle target verification")
    return value


def _target_directory(candidate: Path, subpath: str) -> Path:
    relative = PurePosixPath(subpath)
    if subpath and (
        relative.is_absolute()
        or relative.as_posix() != subpath
        or "\\" in subpath
        or any(part in {".", ".."} for part in relative.parts)
    ):
        raise ValueError("authorized target subpath is not canonical")
    _no_links(candidate)
    directory = candidate if not subpath else candidate / subpath
    _no_links(directory)
    if not directory.is_dir() or not directory.resolve().is_relative_to(candidate.resolve()):
        raise ValueError("target verification directory is missing or escapes the candidate")
    return directory


def _open_directory(path: Path) -> int:
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _retain_plan(path: Path, plan: dict[str, Any], *, existing_required: bool) -> None:
    _no_links(path)
    expected = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if path.exists():
        if _read(path, "retained target verification plan") != expected:
            raise ValueError(
                "retained verification plan differs from its exact target/host subject"
            )
        return
    if existing_required:
        raise ValueError("target verification plan is missing")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(expected)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    if _read(path, "published target verification plan") != expected:
        raise ValueError("target verification plan changed during publication")


def execute(arguments: argparse.Namespace) -> None:
    root = Path(os.path.abspath(arguments.run_root))
    manifest_path = root / "evidence" / "target-resolution" / "target-manifest.toml"
    _no_links(manifest_path)
    raw = _read(manifest_path, "retained target manifest")
    manifest = load_target_manifest_bytes(raw, source_label=str(manifest_path))
    if manifest.source_digest != arguments.expected_manifest_digest:
        raise ValueError("retained target manifest differs from the authorized manifest digest")
    if manifest.repo.get("subpath", "") != arguments.subpath:
        raise ValueError("retained manifest subpath differs from the resolved target")
    candidate = Path(os.path.abspath(arguments.candidate_root))
    cwd = _target_directory(candidate, arguments.subpath)
    build = _argv(manifest.build.get("build_command"), "build_command", optional=True)
    tests = _argv(manifest.build.get("test_command"), "test_command")
    plan = {
        "schema_version": "factory-target-endgame-verification/1",
        "candidate_sha": arguments.candidate_sha,
        "candidate_root": str(candidate),
        "target_workdir": str(cwd),
        "target_state_digest": arguments.expected_target_state_digest,
        "target_manifest_digest": manifest.source_digest,
        "build_command": build,
        "test_command": tests,
        "target_execution_environment": {
            "removed_controls": list(_TARGET_EXECUTION_CONTROLS),
            "receipt_environment": _receipt_environment(root),
        },
        "host_isolation": _host_identity(root),
    }
    plan_path = Path(os.path.abspath(arguments.plan_path))
    if plan_path.parent != root / "endgame":
        raise ValueError("verification plan must be retained in the run endgame directory")
    _retain_plan(
        plan_path, plan, existing_required=(arguments.check_only or arguments.host_isolation)
    )
    if arguments.check_only:
        print("target/host verification plan: current")
        return
    if arguments.plan_only:
        print("target/host verification plan: retained; no target code executed")
        return
    receipt = Path(__file__).resolve().with_name("receipt.sh")
    if arguments.host_isolation:
        proof = plan["host_isolation"]
        result = subprocess.run(
            [proof["receipt_interpreter"]["path"], str(receipt), *proof["argv"]],
            cwd=FACTORY_ROOT,
            env=proof["environment"],
            check=False,
        )
        if result.returncode:
            raise ValueError("host isolation proof failed")
        if _host_identity(root) != proof:
            raise ValueError("host isolation proof source/tool identity changed during execution")
        print("host isolation backend proof: passed; actual run lane receipts remain separate")
        return
    environment = {
        key: value for key, value in os.environ.items() if key not in _TARGET_EXECUTION_CONTROLS
    }
    environment.update(_receipt_environment(root))
    for label, argv in (("target build", build), ("target tests", tests)):
        if not argv:
            continue
        print(f"== {label} ==", flush=True)
        # Recheck immediately before launching; argv is already captured from authorized bytes.
        if _read(manifest_path, "retained target manifest") != raw:
            raise ValueError("retained target manifest changed before verification execution")
        _target_directory(candidate, arguments.subpath)
        descriptor = _open_directory(cwd)
        original_directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # macOS does not support chdir through /dev/fd/<directory-fd>. In this
            # single-threaded helper, fchdir anchors the child to the opened inode.
            os.fchdir(descriptor)
            result = subprocess.run(
                [plan["host_isolation"]["receipt_interpreter"]["path"], str(receipt), *argv],
                env=environment,
                check=False,
            )
            _target_directory(candidate, arguments.subpath)
            named = cwd.stat(follow_symlinks=False)
            opened = os.fstat(descriptor)
            if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("target verification directory changed during execution")
        finally:
            os.fchdir(original_directory)
            os.close(original_directory)
            os.close(descriptor)
        if result.returncode:
            raise ValueError(f"{label} failed; target verification cannot advance")
    if _read(manifest_path, "retained target manifest") != raw:
        raise ValueError("retained target manifest changed during verification execution")


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in (
        "run-root",
        "expected-manifest-digest",
        "expected-target-state-digest",
        "candidate-root",
        "candidate-sha",
        "subpath",
        "plan-path",
    ):
        parser.add_argument(f"--{name}", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check-only", action="store_true")
    modes.add_argument("--plan-only", action="store_true")
    modes.add_argument("--host-isolation", action="store_true")
    try:
        execute(parser.parse_args())
    except (OSError, ValueError) as exc:
        print(f"target-verification: refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
