"""Platform-enforced process isolation for Factory lanes.

Separate directories are organization, not isolation. On macOS this backend uses the kernel
sandbox through ``sandbox-exec`` with deny-by-default file and network access. It deliberately
fails closed on unsupported platforms or an absent executable. The profile is qualified by
real denial probes before a lane is trusted.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class IsolationError(RuntimeError):
    """A lane could not be run under a demonstrated isolation boundary."""


@dataclass(frozen=True)
class IsolatedProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class IsolationQualification:
    backend: str
    read_denied: bool
    write_denied: bool
    network_denied: bool

    @property
    def satisfied(self) -> bool:
        return self.read_denied and self.write_denied and self.network_denied


def _scheme_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _path_filter(path: Path) -> str:
    resolved = path.resolve()
    operator = "subpath" if resolved.is_dir() else "literal"
    return f"({operator} {_scheme_string(str(resolved))})"


def _interpreter_read_paths() -> tuple[Path, ...]:
    """Return the paths the running interpreter must read to start.

    Factory currently runs every lane command through ``sys.executable``. Under a virtualenv,
    CPython needs both the environment at ``sys.prefix`` and the installation at
    ``sys.base_prefix``. Deriving those locations keeps the deny-default profile portable across
    Homebrew, python.org, virtualenv, and hosted-runner layouts without granting the filesystem
    root. These read-only locations are part of the host's trusted computing base: an actor that
    can replace the interpreter already controls Factory before Seatbelt starts. They are
    resolved by the parent process, before any sandboxed lane runs.
    """

    candidates = (
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.executable).resolve().parent,
    )
    seen: dict[Path, None] = {}
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.parent != resolved and resolved.is_dir():
            seen.setdefault(resolved, None)
    return tuple(seen)


class MacOSSandbox:
    """Deny-by-default macOS Seatbelt wrapper with explicit filesystem grants."""

    def __init__(
        self,
        executable: str | Path = "/usr/bin/sandbox-exec",
        *,
        timeout_seconds: int = 120,
    ) -> None:
        self.executable = Path(executable)
        self.timeout_seconds = timeout_seconds
        if platform.system() != "Darwin":
            raise IsolationError("the macOS sandbox backend is unavailable on this platform")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise IsolationError(f"sandbox executable is unavailable: {self.executable}")
        self.interpreter_read_paths = _interpreter_read_paths()
        if not self.interpreter_read_paths:
            raise IsolationError("the trusted interpreter has no safe readable path grant")

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path] = (),
        writable_paths: Sequence[str | Path] = (),
        environment: Mapping[str, str] | None = None,
    ) -> IsolatedProcessResult:
        """Run with no ambient environment, network, or user-file access."""

        if not command or not all(str(part) for part in command):
            raise IsolationError("isolated command cannot be empty")
        working_directory = Path(cwd).resolve()
        if not working_directory.is_dir():
            raise IsolationError(f"isolated working directory does not exist: {working_directory}")
        readable = tuple(Path(path).resolve() for path in readable_paths)
        writable = tuple(Path(path).resolve() for path in writable_paths)
        if not any(
            working_directory == path or working_directory.is_relative_to(path)
            for path in writable
        ):
            raise IsolationError("isolated working directory must be inside a writable grant")
        for path in (*readable, *writable):
            if not path.exists():
                raise IsolationError(f"isolated path grant does not exist: {path}")

        profile = self._profile(readable, writable)
        fd, profile_path = tempfile.mkstemp(
            prefix=".factory-sandbox-",
            suffix=".sb",
            dir=working_directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(profile)
                handle.flush()
                os.fsync(handle.fileno())
            lane_environment = {
                "HOME": str(working_directory),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(working_directory),
                **dict(environment or {}),
            }
            completed = subprocess.run(
                [
                    str(self.executable),
                    "-f",
                    profile_path,
                    *[str(part) for part in command],
                ],
                cwd=working_directory,
                env=lane_environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsolationError(f"isolated process execution failed: {exc}") from exc
        finally:
            if os.path.exists(profile_path):
                os.unlink(profile_path)
        return IsolatedProcessResult(
            command=tuple(str(part) for part in command),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def qualify(self, root: str | Path) -> IsolationQualification:
        """Attempt forbidden read, write, and network operations and require refusal."""

        qualification_root = Path(root).resolve()
        qualification_root.mkdir(parents=True, exist_ok=True)
        allowed = qualification_root / "allowed"
        forbidden = qualification_root / "forbidden"
        allowed.mkdir()
        forbidden.mkdir()
        secret = forbidden / "secret.txt"
        secret.write_text("must-not-be-readable", encoding="utf-8")
        outside_write = forbidden / "must-not-be-created.txt"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener_port = listener.getsockname()[1]
        probe = """
import json
import os
import socket

result = {}
try:
    open(os.environ["FACTORY_FORBIDDEN_READ"], encoding="utf-8").read()
    result["read_denied"] = False
except OSError:
    result["read_denied"] = True
try:
    open(os.environ["FACTORY_FORBIDDEN_WRITE"], "w", encoding="utf-8").write("bad")
    result["write_denied"] = False
except OSError:
    result["write_denied"] = True
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    result["bind_denied"] = False
except OSError:
    result["bind_denied"] = True
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", int(os.environ["FACTORY_LISTENER_PORT"])))
    result["connect_denied"] = False
except OSError:
    result["connect_denied"] = True
result["network_denied"] = result["bind_denied"] and result["connect_denied"]
print(json.dumps(result, sort_keys=True))
"""
        try:
            process = self.run(
                (sys.executable, "-c", probe),
                cwd=allowed,
                writable_paths=(allowed,),
                environment={
                    "FACTORY_FORBIDDEN_READ": str(secret),
                    "FACTORY_FORBIDDEN_WRITE": str(outside_write),
                    "FACTORY_LISTENER_PORT": str(listener_port),
                },
            )
        finally:
            listener.close()
        if process.returncode != 0:
            raise IsolationError(
                "sandbox qualification process failed: "
                + (process.stderr.strip() or process.stdout.strip() or "no diagnostic")
            )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise IsolationError("sandbox qualification returned invalid evidence") from exc
        qualification = IsolationQualification(
            backend="macos-seatbelt",
            read_denied=result.get("read_denied") is True,
            write_denied=result.get("write_denied") is True,
            network_denied=result.get("network_denied") is True,
        )
        if not qualification.satisfied:
            raise IsolationError(f"sandbox denial qualification failed: {qualification}")
        if outside_write.exists():
            raise IsolationError("sandbox write-denial probe created the forbidden file")
        return qualification

    def _profile(
        self,
        readable_paths: Sequence[Path],
        writable_paths: Sequence[Path],
    ) -> str:
        read_filters = "\n    ".join(
            _path_filter(path)
            for path in (*self.interpreter_read_paths, *readable_paths, *writable_paths)
        )
        write_filters = "\n    ".join(_path_filter(path) for path in writable_paths)
        return f"""(version 1)
(deny default)
(import "system.sb")
(allow process*)
(allow file-read-metadata)
(allow file-read* file-map-executable
    (subpath "/opt/homebrew")
    (subpath "/usr/local")
    (subpath "/bin")
    (subpath "/private/etc")
    {read_filters})
(allow file-write*
    {write_filters})
"""
