"""macOS enforcement backend for the networked outer model runner.

The model client may reach its provider, but it receives only a private projection workspace.
Seatbelt denies every unlisted file effect and every process exec except the exact runner chain;
the parent additionally supervises the whole process group for wall, idle, output, and process
ceilings.  Qualification exercises the same profile generator and supervisor before dispatch.
"""

from __future__ import annotations

import json
import os
import platform
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from factory_core.manifest import digest_bytes, digest_obj
from factory_runtime.runner import (
    NetworkedRunnerBackend,
    RunnerError,
    RunnerLimits,
    RunnerProcessResult,
    RunnerQualification,
)
from factory_runtime.runner_termination import (
    COMPLETED,
    EXIT_NONZERO,
    IDLE_LIMIT,
    OUTPUT_LIMIT,
    PROCESS_ESCAPE,
    PROCESS_LIMIT,
    WALL_LIMIT,
)

_POLL_SECONDS = 0.10
_MAX_STDIN_BYTES = 2_097_152


def _scheme_string(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RunnerError("Seatbelt path contains a forbidden control character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _filter(path: Path) -> str:
    operator = "subpath" if path.is_dir() else "literal"
    return f"({operator} {_scheme_string(str(path))})"


def _is_overlapping(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _write_regular_once(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RunnerError("runner isolation artifact is not regular")
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _process_group_members(group_id: int) -> tuple[int, ...]:
    try:
        completed = subprocess.run(
            ("/bin/ps", "-axo", "pid=,pgid="),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    members: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, pgid = (int(part) for part in parts)
        except ValueError:
            continue
        if pgid == group_id:
            members.append(pid)
    return tuple(sorted(members))


def _kill_group(group_id: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group_id, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            # Some macOS sandboxed descendants reject a group-directed signal even though the
            # same parent may signal each same-uid member.  Enumerate the already captured group
            # rather than weakening containment or leaving the tree alive.
            for pid in reversed(_process_group_members(group_id)):
                try:
                    os.kill(pid, sig)
                except (PermissionError, ProcessLookupError):
                    continue
        deadline = time.monotonic() + (0.5 if sig == signal.SIGTERM else 1.0)
        while time.monotonic() < deadline:
            if not _process_group_members(group_id):
                return
            time.sleep(0.05)


def _find_output_path(command: Sequence[str]) -> Path:
    try:
        index = tuple(command).index("--output-last-message")
        value = command[index + 1]
    except (ValueError, IndexError) as exc:
        raise RunnerError("runner command has no fixed output-last-message destination") from exc
    return Path(value).resolve()


def _event_facts(stdout: str) -> tuple[str, int, int]:
    session_id = ""
    input_tokens = 0
    output_tokens = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            session_id = str(event["thread_id"])
        usage = event.get("usage")
        if isinstance(usage, Mapping):
            candidate_input = usage.get("input_tokens")
            candidate_output = usage.get("output_tokens")
            if isinstance(candidate_input, int) and candidate_input >= 0:
                input_tokens = candidate_input
            if isinstance(candidate_output, int) and candidate_output >= 0:
                output_tokens = candidate_output
    return session_id, input_tokens, output_tokens


class MacOSNetworkedRunner(NetworkedRunnerBackend):
    """Seatbelt-backed networked runner with exact-exec and process-group containment."""

    def __init__(self, executable: str | Path = "/usr/bin/sandbox-exec") -> None:
        self.executable = Path(executable).resolve()
        if platform.system() != "Darwin":
            raise RunnerError("the hardened networked runner requires macOS Seatbelt")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise RunnerError(f"sandbox executable is unavailable: {self.executable}")
        self._allowed_executables: tuple[Path, ...] = ()
        self._forbidden_paths: tuple[Path, ...] = ()
        self._workspace_root: Path | None = None
        self._scope_digest = ""

    def qualify(
        self,
        root: str | Path,
        *,
        allowed_executables: Sequence[str | Path],
        forbidden_paths: Sequence[str | Path],
    ) -> RunnerQualification:
        qualification_root = Path(root).resolve()
        if qualification_root.exists() or qualification_root.is_symlink():
            raise RunnerError("runner qualification root must be fresh")
        qualification_root.mkdir(parents=True)
        allowed = tuple(Path(path).resolve(strict=True) for path in allowed_executables)
        if not allowed or len(set(allowed)) != len(allowed):
            raise RunnerError("runner qualification requires unique exact executables")
        for executable in allowed:
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise RunnerError(f"runner executable cannot be qualified: {executable}")
        forbidden = tuple(Path(path).resolve(strict=True) for path in forbidden_paths)
        if not forbidden or len(set(forbidden)) != len(forbidden):
            raise RunnerError("runner qualification requires unique forbidden roots")
        workspace_root = qualification_root.parent.resolve()
        if any(_is_overlapping(workspace_root, path) for path in forbidden):
            raise RunnerError("runner private workspace overlaps a forbidden root")
        scope_digest = digest_obj(
            {
                "allowed_executables": [
                    {"path": str(path), "digest": digest_bytes(path.read_bytes())}
                    for path in allowed
                ],
                "forbidden_paths": [str(path) for path in forbidden],
                "workspace_root": str(workspace_root),
                "profile_version": "factory-networked-seatbelt/1",
            }
        )

        probe_allowed = qualification_root / "allowed"
        probe_forbidden = qualification_root / "forbidden"
        probe_allowed.mkdir()
        probe_forbidden.mkdir()
        secret = probe_forbidden / "secret.txt"
        secret.write_text("must-not-be-readable", encoding="utf-8")
        outside_write = probe_forbidden / "must-not-exist.txt"
        probe_path = probe_allowed / "probe.py"
        probe_path.write_text(
            """import json
import os
import socket
import subprocess

result = {}
try:
    open(os.environ["DENIED_READ"], encoding="utf-8").read()
    result["read_denied"] = False
except OSError:
    result["read_denied"] = True
try:
    open(os.environ["DENIED_WRITE"], "w", encoding="utf-8").write("bad")
    result["write_denied"] = False
except OSError:
    result["write_denied"] = True
try:
    connection = socket.create_connection(
        ("127.0.0.1", int(os.environ["PORT"])), timeout=2
    )
    connection.close()
    result["network"] = True
except OSError:
    result["network"] = False
try:
    subprocess.run(["/bin/sh", "-c", "true"], check=False)
    result["shell_denied"] = False
except OSError:
    result["shell_denied"] = True
print(json.dumps(result, sort_keys=True))
""",
            encoding="utf-8",
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        qualification_python = (
            Path(sys.base_prefix) / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
        ).resolve(strict=True)
        probe_limits = RunnerLimits(10, 5, 2, 3, 65_536, 1, 0)
        try:
            probe = self._supervised(
                (str(qualification_python), str(probe_path)),
                cwd=probe_allowed,
                readable_paths=(probe_path, qualification_python),
                writable_paths=(probe_allowed,),
                environment={
                    "HOME": str(probe_allowed),
                    "TMPDIR": str(probe_allowed),
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "DENIED_READ": str(secret),
                    "DENIED_WRITE": str(outside_write),
                    "PORT": str(listener.getsockname()[1]),
                },
                stdin=b"",
                limits=probe_limits,
                allowed_executables=(qualification_python,),
            )
            listener.settimeout(2)
            try:
                connection, _ = listener.accept()
                connection.close()
            except OSError:
                pass
        finally:
            listener.close()
        try:
            facts = json.loads(probe.stdout.strip())
        except json.JSONDecodeError as exc:
            diagnostic = probe.stderr.strip() or probe.stdout.strip() or "no output"
            raise RunnerError(
                "networked runner qualification returned invalid evidence: "
                f"{probe.termination_reason}/{probe.returncode}: {diagnostic}"
            ) from exc

        tree_probe = probe_allowed / "tree.py"
        tree_probe.write_text(
            "import os, subprocess, time\n"
            "children=[subprocess.Popen([os.environ['QUAL_PYTHON'],'-c',"
            "'import time; time.sleep(20)']) "
            "for _ in range(3)]\n"
            "time.sleep(20)\n",
            encoding="utf-8",
        )
        tree = self._supervised(
            (str(qualification_python), str(tree_probe)),
            cwd=probe_allowed,
            readable_paths=(tree_probe, qualification_python),
            writable_paths=(probe_allowed,),
            environment={
                "HOME": str(probe_allowed),
                "TMPDIR": str(probe_allowed),
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "QUAL_PYTHON": str(qualification_python),
            },
            stdin=b"",
            limits=RunnerLimits(10, 5, 2, 3, 65_536, 1, 0),
            allowed_executables=(qualification_python,),
        )
        qualification = RunnerQualification(
            backend="macos-seatbelt-networked-v1",
            scope_digest=scope_digest,
            forbidden_read_denied=facts.get("read_denied") is True,
            forbidden_write_denied=facts.get("write_denied") is True
            and not outside_write.exists(),
            model_network_available=facts.get("network") is True,
            arbitrary_shell_denied=facts.get("shell_denied") is True,
            process_containment=(
                tree.termination_reason == PROCESS_LIMIT and tree.process_peak > 2
            ),
        )
        if not qualification.satisfied:
            raise RunnerError(
                f"networked runner qualification failed: {qualification}; "
                f"tree={tree.termination_reason}/{tree.returncode}/peak={tree.process_peak}: "
                f"{tree.stderr.strip()}"
            )
        self._allowed_executables = allowed
        self._forbidden_paths = forbidden
        self._workspace_root = workspace_root
        self._scope_digest = scope_digest
        return qualification

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        readable_paths: Sequence[str | Path],
        writable_paths: Sequence[str | Path],
        environment: Mapping[str, str],
        stdin: bytes,
        limits: RunnerLimits,
    ) -> RunnerProcessResult:
        if not self._allowed_executables or self._workspace_root is None:
            raise RunnerError("networked runner was not qualified for this dispatch")
        if not command or Path(command[0]).resolve() != self._allowed_executables[0]:
            raise RunnerError("runner command does not start with the qualified executable")
        working = Path(cwd).resolve(strict=True)
        if working != self._workspace_root and not working.is_relative_to(self._workspace_root):
            raise RunnerError("runner cwd escapes the qualified private workspace")
        readable = tuple(Path(path).resolve(strict=True) for path in readable_paths)
        writable = tuple(Path(path).resolve(strict=True) for path in writable_paths)
        for grant in (*readable, *writable):
            if any(_is_overlapping(grant, denied) for denied in self._forbidden_paths):
                raise RunnerError("runner grant overlaps a forbidden target/control root")
        output_path = _find_output_path(command)
        if not any(output_path == path or output_path.is_relative_to(path) for path in writable):
            raise RunnerError("runner output destination is outside its writable grant")
        process = self._supervised(
            command,
            cwd=working,
            readable_paths=readable,
            writable_paths=writable,
            environment=environment,
            stdin=stdin,
            limits=limits,
            allowed_executables=self._allowed_executables,
            counts_as_model_attempt=True,
        )
        if process.returncode != 0 or process.termination_reason != "completed":
            return process
        if output_path.is_symlink() or not output_path.is_file():
            return RunnerProcessResult(
                **{**process.__dict__, "structured_output": {}, "termination_reason": "no-artifact"}
            )
        raw = output_path.read_bytes()
        if len(raw) > limits.max_output_bytes:
            return RunnerProcessResult(
                **{
                    **process.__dict__,
                    "structured_output": {},
                    "termination_reason": "output-limit",
                }
            )
        try:
            structured = json.loads(raw)
        except json.JSONDecodeError:
            structured = {}
        if not isinstance(structured, Mapping):
            structured = {}
        session_id, input_tokens, output_tokens = _event_facts(process.stdout)
        return RunnerProcessResult(
            command=process.command,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            structured_output=dict(structured),
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            process_peak=process.process_peak,
            termination_reason=process.termination_reason,
        )

    def _supervised(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        readable_paths: Sequence[Path],
        writable_paths: Sequence[Path],
        environment: Mapping[str, str],
        stdin: bytes,
        limits: RunnerLimits,
        allowed_executables: Sequence[Path],
        counts_as_model_attempt: bool = False,
    ) -> RunnerProcessResult:
        if len(stdin) > _MAX_STDIN_BYTES:
            raise RunnerError("runner stdin exceeds its bounded input size")
        for key, value in environment.items():
            if not key or "=" in key or "\x00" in key or "\x00" in value:
                raise RunnerError("runner environment contains an invalid entry")
            if key.startswith(("DYLD_", "LD_")) or key in {
                "BASH_ENV",
                "ENV",
                "PYTHONPATH",
                "PYTHONHOME",
                "SHELLOPTS",
            }:
                raise RunnerError(f"runner environment contains a loader/shell control: {key}")
        profile = self._profile(
            readable_paths=tuple(readable_paths),
            writable_paths=tuple(writable_paths),
            allowed_executables=tuple(allowed_executables),
        )
        profile_path = cwd / f".runner-{os.getpid()}-{time.monotonic_ns()}.sb"
        _write_regular_once(profile_path, profile.encode("utf-8"))
        started = time.monotonic()
        last_activity = started
        stdout = bytearray()
        stderr = bytearray()
        process_peak = 0
        reason = COMPLETED
        process: subprocess.Popen[bytes] | None = None
        selector = selectors.DefaultSelector()
        try:
            process = subprocess.Popen(
                (str(self.executable), "-f", str(profile_path), *map(str, command)),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise RunnerError("runner process pipes were not created")
            pending_stdin = memoryview(stdin)
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stderr.fileno(), False)
            if pending_stdin:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            selector.register(process.stdout, selectors.EVENT_READ, stdout)
            selector.register(process.stderr, selectors.EVENT_READ, stderr)
            while True:
                now = time.monotonic()
                members = _process_group_members(process.pid)
                process_peak = max(process_peak, len(members))
                if len(members) > limits.max_processes:
                    reason = PROCESS_LIMIT
                    _kill_group(process.pid)
                elif now - started > limits.wall_seconds:
                    reason = WALL_LIMIT
                    _kill_group(process.pid)
                elif now - last_activity > limits.idle_seconds:
                    reason = IDLE_LIMIT
                    _kill_group(process.pid)
                events = selector.select(_POLL_SECONDS)
                for selector_key, _ in events:
                    if selector_key.data == "stdin":
                        try:
                            written = os.write(selector_key.fd, pending_stdin)
                        except BrokenPipeError:
                            written = 0
                        except BlockingIOError:
                            continue
                        if written > 0:
                            pending_stdin = pending_stdin[written:]
                            last_activity = time.monotonic()
                        if written <= 0 or not pending_stdin:
                            selector.unregister(selector_key.fileobj)
                            process.stdin.close()
                        continue
                    try:
                        chunk = os.read(selector_key.fd, 65_536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        selector_key.data.extend(chunk)
                        last_activity = time.monotonic()
                    else:
                        selector.unregister(selector_key.fileobj)
                if len(stdout) + len(stderr) > limits.max_output_bytes:
                    reason = OUTPUT_LIMIT
                    _kill_group(process.pid)
                if process.poll() is not None and not selector.get_map():
                    break
            returncode = process.wait(timeout=2)
            if _process_group_members(process.pid):
                reason = PROCESS_ESCAPE
                _kill_group(process.pid)
            if returncode != 0 and reason == COMPLETED:
                reason = EXIT_NONZERO
        except (OSError, subprocess.SubprocessError, RunnerError) as exc:
            if process is not None:
                _kill_group(process.pid)
            attempts = 1 if counts_as_model_attempt and process is not None else 0
            raise RunnerError(
                f"networked runner process failed: {exc}",
                model_attempts=max(attempts, getattr(exc, "model_attempts", 0)),
            ) from exc
        finally:
            selector.close()
            if process is not None and process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            try:
                profile_path.unlink()
            except FileNotFoundError:
                pass
        return RunnerProcessResult(
            command=tuple(map(str, command)),
            returncode=returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            structured_output={},
            session_id="",
            input_tokens=0,
            output_tokens=0,
            process_peak=max(process_peak, 1),
            termination_reason=reason,
        )

    @staticmethod
    def _profile(
        *,
        readable_paths: Sequence[Path],
        writable_paths: Sequence[Path],
        allowed_executables: Sequence[Path],
    ) -> str:
        reads = "\n    ".join(_filter(path) for path in (*readable_paths, *writable_paths))
        writes = "\n    ".join(_filter(path) for path in writable_paths)
        executions = "\n    ".join(
            f"(literal {_scheme_string(str(path.resolve()))})" for path in allowed_executables
        )
        return f"""(version 1)
(deny default)
(import "system.sb")
(allow process-fork)
(allow process-exec
    {executions})
(allow file-read-metadata)
(allow file-read* file-map-executable
    (subpath "/System")
    (subpath "/usr/lib")
    (subpath "/usr/share")
    (subpath "/private/etc")
    (subpath "/private/var/db/dyld")
    (subpath "/opt/homebrew")
    (literal "/dev/null")
    (literal "/dev/urandom")
    {reads})
(allow file-write*
    (literal "/dev/null")
    {writes})
(allow network-outbound)
"""


__all__ = ["MacOSNetworkedRunner"]
