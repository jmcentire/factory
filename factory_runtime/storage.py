"""Content-preserving opt-in archival; modes and xattrs retained, ownership not restored.

Archival preserves dirty files and local Git objects rather than deciding which work
is disposable. No automatic deletion, no remote upload, no cross-run pruning.
"""
from __future__ import annotations

import base64
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from factory_runtime.kindex_workspace import NO_INDEX_MARKER, mark_no_index

SCHEMA = "factory-run-archive/1"


@contextmanager
def run_storage_lock(root: Path) -> Iterator[None]:
    """Serialize run startup and archival without writing into the archived tree."""
    path = root.parent / f".{root.name}.storage.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or name in {".", ".."}:
        raise ValueError("invalid run name")
    return name


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


@lru_cache(maxsize=3)
def _darwin_xattr(operation: str) -> Any:
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    if operation == "listxattr":
        function = library.listxattr
    elif operation == "getxattr":
        function = library.getxattr
    elif operation == "setxattr":
        function = library.setxattr
    else:
        raise ValueError("unsupported extended-attribute operation")
    if operation == "listxattr":
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    else:
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                             ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    function.restype = ctypes.c_int if operation == "setxattr" else ctypes.c_ssize_t
    return function


def _xattr_result(result: int, path: Path) -> int:
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(path))
    return result


def _xattrs(path: Path) -> dict[str, str]:
    getter = getattr(os, "getxattr", None)
    if hasattr(os, "listxattr") and getter is not None:
        return {name: base64.b64encode(getter(path, name, follow_symlinks=False)).decode()
                for name in sorted(os.listxattr(path, follow_symlinks=False))}
    if sys.platform != "darwin":
        raise ValueError("extended-attribute inspection is unsupported on this platform")
    listing = _darwin_xattr("listxattr")
    try:
        size = _xattr_result(listing(os.fsencode(path), None, 0, 1), path)
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            return {}
        raise
    names = ctypes.create_string_buffer(size)
    size = _xattr_result(listing(os.fsencode(path), names, size, 1), path)
    result = {}
    get = _darwin_xattr("getxattr")
    for name in sorted(names.raw[:size].split(b"\0")):
        if not name:
            continue
        length = _xattr_result(get(os.fsencode(path), name, None, 0, 0, 1), path)
        data = ctypes.create_string_buffer(length)
        length = _xattr_result(get(os.fsencode(path), name, data, length, 0, 1), path)
        result[os.fsdecode(name)] = base64.b64encode(data.raw[:length]).decode()
    return result


def _restore_xattrs(path: Path, attributes: dict[str, str]) -> list[str]:
    failures = []
    for name, encoded in attributes.items():
        value = base64.b64decode(encoded, validate=True)
        try:
            if hasattr(os, "setxattr"):
                os.setxattr(path, name, value, follow_symlinks=False)
            elif sys.platform == "darwin":
                _xattr_result(_darwin_xattr("setxattr")(
                    os.fsencode(path), os.fsencode(name), value, len(value), 0, 1), path)
            else:
                raise OSError("extended-attribute restoration unsupported")
        except OSError:
            failures.append(name)
    actual = _xattrs(path)
    failed = sorted(set(failures) | {name for name, value in attributes.items()
                                    if actual.get(name) != value})
    if any(name != "com.apple.provenance" or sys.platform != "darwin" for name in failed):
        raise ValueError("extended attributes could not be restored; archive retained")
    return failed


def _attribute_header(attributes: dict[str, str]) -> str:
    return json.dumps(attributes, sort_keys=True, separators=(",", ":"))


def _entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(current) / name
            info = path.lstat()
            rel = path.relative_to(root).as_posix()
            record: dict[str, Any] = {"path": rel, "mode": stat.S_IMODE(info.st_mode),
                                      "xattrs": _xattrs(path)}
            if stat.S_ISLNK(info.st_mode):
                if name == ".git":
                    raise ValueError(f"external Git directory must be preserved separately: {rel}")
                record.update(kind="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                record.update(kind="directory")
            elif stat.S_ISREG(info.st_mode):
                if name == ".git":
                    raise ValueError(f"linked Git worktree requires separate preservation: {rel}")
                if rel.endswith(".git/objects/info/alternates") and path.read_text().strip():
                    raise ValueError(f"external Git objects must be preserved separately: {rel}")
                record.update(kind="file", size=info.st_size, sha256=_digest(path))
            else:
                raise ValueError(f"unsupported special file: {rel}")
            entries.append(record)
    return sorted(entries, key=lambda item: item["path"])


def _json_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f"invalid record: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"invalid record: {path}")
    return value


def _pid_inactive(pid: object) -> None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        raise ValueError("invalid process id")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise ValueError(f"cannot establish process inactivity: {pid}") from exc
    raise ValueError(f"run process {pid} is still active")


def _active(root: Path) -> None:
    """Check known process records and exact run socket, never kill anything."""
    record = _json_record(root / "harness.json")
    if record.get("schema_version") != "factory-harness/3" or record.get("run_id") != root.name:
        raise ValueError("unknown run ownership/schema; only ordinary Factory runs are supported")
    socket = record.get("tmux_socket", "")
    expected = "factory-" + hashlib.sha256(str(root).encode()).hexdigest()[:20]
    if socket != expected:
        raise ValueError("run socket does not match its directory")
    if shutil.which("tmux") is None:
        raise ValueError("cannot establish inactivity: tmux unavailable")
    probe = subprocess.run(["tmux", "-L", socket, "list-sessions"],
                           capture_output=True, check=False)
    if probe.returncode == 0:
        raise ValueError("run still has an active tmux session")
    error = probe.stderr.decode(errors="replace").lower()
    if not any(text in error for text in ("no server running", "no such file or directory")):
        raise ValueError("cannot establish tmux inactivity: " + error.strip())
    for path in root.rglob("*-process.json"):
        process = _json_record(path)
        if process.get("schema") != "factory-worker-process/1":
            raise ValueError(f"unknown process record: {path}")
        for field in ("worker", "wrapper"):
            identity = process.get(field)
            if identity is not None:
                if not isinstance(identity, dict):
                    raise ValueError(f"invalid process identity: {path}")
                _pid_inactive(identity.get("pid"))
    for path in root.rglob("*.pid"):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unreadable process record: {path}")
        raw = path.read_text().strip()
        if not raw.isdigit():
            raise ValueError(f"invalid process record: {path}")
        _pid_inactive(int(raw))


def _usage(root: Path) -> dict[str, int]:
    totals = {"bytes": 0, "git_bytes": 0, "log_bytes": 0, "generated_bytes": 0,
              "git_repositories": 0}
    for current, dirs, files in os.walk(root, followlinks=False):
        if ".git" in dirs or ".git" in files:
            totals["git_repositories"] += 1
        for name in files:
            item = Path(current) / name
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            size = info.st_size
            totals["bytes"] += size
            parts = item.relative_to(root).parts
            if ".git" in parts:
                totals["git_bytes"] += size
            elif any(part in {"Library", "node_modules", ".venv", "__pycache__"} for part in parts):
                totals["generated_bytes"] += size
            elif name.endswith((".log", ".jsonl")):
                totals["log_bytes"] += size
    return totals


def _external_resources(root: Path) -> list[dict[str, Any]]:
    resources = []
    for role in ("coder", "tester"):
        path = root / "inputs" / f"{role}.json"
        if not path.exists():
            continue
        record = _json_record(path)
        value = record.get("repository")
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"invalid repository reference: {path}")
        external = Path(value)
        if external.resolve().is_relative_to(root.resolve()):
            continue
        resources.append({"role": role, "path": value,
                          "scope": "referenced-external-not-archived",
                          "bytes": _usage(external)["bytes"] if external.is_dir() else None})
    return resources


def inventory(runs: Path) -> dict[str, Any]:
    runs = runs.expanduser().resolve(strict=True)
    records = []
    archives: list[dict[str, Any]] = []
    for path in sorted(runs.iterdir()):
        if not path.is_symlink() and path.is_file() and path.name.endswith(".tar.gz"):
            archives.append({"path": str(path), "bytes": path.stat().st_size})
        if path.is_symlink() or not path.is_dir():
            continue
        record: dict[str, Any] = {"run": path.name, **_usage(path)}
        try:
            record["external_resources"] = _external_resources(path)
        except (OSError, ValueError) as exc:
            record["external_resources_error"] = str(exc)
        records.append(record)
    run_bytes = sum(item["bytes"] for item in records)
    archive_bytes = sum(item["bytes"] for item in archives)
    return {"runs": records, "total_bytes": run_bytes,
            "archives": archives, "total_archive_bytes": archive_bytes,
            "total_storage_bytes": run_bytes + archive_bytes,
            "scope": "total_storage_bytes counts run-root files plus local tar.gz archives; "
                     "excludes external resources, external archives and receipt/lock files"}


def archive_run(runs: Path, run: str, *, completed: bool, apply: bool,
                archive_dir: Path | None = None) -> dict[str, Any]:
    root = runs.expanduser().resolve(strict=True) / _name(run)
    if not apply:
        return _archive_run(runs, run, completed=completed, apply=False, archive_dir=archive_dir)
    with run_storage_lock(root):
        return _archive_run(runs, run, completed=completed, apply=apply, archive_dir=archive_dir)


def _archive_run(runs: Path, run: str, *, completed: bool, apply: bool,
                 archive_dir: Path | None) -> dict[str, Any]:
    runs = runs.expanduser().resolve(strict=True)
    root = runs / _name(run)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("run must be an existing non-symlink directory")
    if not completed:
        raise ValueError("--completed is required: confirm all run work is finished and stopped")
    _active(root)
    root_xattrs = _xattrs(root)
    root_mode = stat.S_IMODE(root.stat().st_mode)
    entries = _entries(root)
    total = sum(item.get("size", 0) for item in entries)
    output_dir = runs if archive_dir is None else archive_dir.expanduser().resolve(strict=True)
    if not output_dir.is_dir() or output_dir.is_relative_to(root):
        raise ValueError("archive directory must exist outside the selected run")
    destination = output_dir / f"{run}.tar.gz"
    receipt = output_dir / f"{run}.archive.json"
    result: dict[str, Any] = {"run": run, "source_bytes": total,
                              "archive": str(destination), "applied": False,
                              "external_resources_not_archived": _external_resources(root)}
    if (destination.exists() or destination.is_symlink()
            or receipt.exists() or receipt.is_symlink()):
        raise ValueError("archive or receipt already exists; refusing overwrite")
    if not apply:
        return result
    # Incompressible inputs can grow slightly; don't turn ENOSPC into lost originals.
    reserve = total + max(16 * 1024 * 1024, total // 100)
    if shutil.disk_usage(output_dir).free < reserve:
        raise ValueError("insufficient free space to safely stage archive; use another volume")
    fd, temporary = tempfile.mkstemp(prefix=f".{run}-", suffix=".tar.gz", dir=output_dir)
    os.close(fd)
    temp = Path(temporary)
    try:
        with tarfile.open(temp, "w:gz", dereference=False) as output:
            root_info = output.gettarinfo(str(root), arcname=run)
            root_info.pax_headers = {"factory.xattrs": _attribute_header(root_xattrs)}
            output.addfile(root_info)
            for item in entries:
                source = root / item["path"]
                info = output.gettarinfo(str(source), arcname=f"{run}/{item['path']}")
                info.pax_headers = {"factory.xattrs": _attribute_header(item["xattrs"])}
                if item["kind"] == "file":
                    # Local clones often hardlink Git objects. Store each regular payload
                    # independently so neither restoration nor verification follows a link.
                    info.type = tarfile.REGTYPE
                    info.linkname = ""
                    info.size = item["size"]
                    with source.open("rb") as content:
                        output.addfile(info, content)
                else:
                    output.addfile(info)
        manifest = {"schema_version": SCHEMA, "run": run, "entries": entries,
                    "root_mode": root_mode, "root_xattrs": root_xattrs,
                    "archive_sha256": _digest(temp)}
        _verify_archive(temp, manifest)
        _active(root)
        if (_entries(root) != entries or _xattrs(root) != root_xattrs
                or stat.S_IMODE(root.stat().st_mode) != root_mode):
            raise ValueError("run changed during archival; originals retained")
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        # Exclusive publication: never overwrite an archive from a concurrent command.
        os.link(temp, destination)
        receipt_fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(receipt_fd, "w") as stream:
            json.dump(manifest, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(output_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        # Only this explicitly selected tree is removed after an equivalent copy verifies.
        shutil.rmtree(root)
        result.update(applied=True, archive_bytes=destination.stat().st_size,
                      receipt=str(receipt))
        return result
    finally:
        temp.unlink(missing_ok=True)


def _verify_archive(path: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA or _digest(path) != manifest["archive_sha256"]:
        raise ValueError("archive checksum or schema mismatch")
    run = _name(manifest["run"])
    root_mode = manifest.get("root_mode")
    if (not isinstance(root_mode, int) or isinstance(root_mode, bool)
            or not 0 <= root_mode <= 0o7777):
        raise ValueError("invalid root mode in archive receipt")
    expected = {}
    for item in manifest["entries"]:
        rel = item["path"]
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or any(part in {"", ".", ".."} for part in rel.split("/"))):
            raise ValueError("unsafe archive receipt path")
        if rel in expected or item.get("kind") not in {"file", "directory", "symlink"}:
            raise ValueError("duplicate or invalid archive receipt entry")
        expected[rel] = item
    for rel in expected:
        for parent in Path(rel).parents:
            if (str(parent) != "."
                    and expected.get(parent.as_posix(), {}).get("kind") != "directory"):
                raise ValueError("archive entry has non-directory parent")
    actual: set[str] = set()
    root_seen = False
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if member.name == run and member.isdir():
                if root_seen or member.mode != root_mode:
                    raise ValueError("duplicate archive root or root mode mismatch")
                root_seen = True
                if (member.pax_headers.get("factory.xattrs")
                        != _attribute_header(manifest["root_xattrs"])):
                    raise ValueError("archive root attributes mismatch")
                continue
            prefix = run + "/"
            if not member.name.startswith(prefix):
                raise ValueError("archive member outside run")
            rel = member.name[len(prefix):]
            if (rel in actual or rel not in expected or ".." in Path(rel).parts
                    or Path(rel).is_absolute() or not rel or "." in rel.split("/")):
                raise ValueError("unexpected or duplicate archive member")
            actual.add(rel)
            item = expected[rel]
            if member.pax_headers.get("factory.xattrs") != _attribute_header(item["xattrs"]):
                raise ValueError("archive attributes mismatch")
            if member.mode != item["mode"]:
                raise ValueError("archive file mode mismatch")
            if item["kind"] == "file":
                if not member.isfile():
                    raise ValueError("archive file type mismatch")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("archive content unavailable")
                digest = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    digest.update(block)
                if digest.hexdigest() != item["sha256"] or member.size != item["size"]:
                    raise ValueError("archive content mismatch")
            elif item["kind"] == "directory" and not member.isdir():
                raise ValueError("archive directory mismatch")
            elif item["kind"] == "symlink" and (
                not member.issym() or member.linkname != item["target"]
            ):
                raise ValueError("archive symlink mismatch")
    if not root_seen or actual != set(expected):
        raise ValueError("archive is incomplete")


def restore_archive(path: Path, destination: Path, *, apply: bool) -> dict[str, Any]:
    path = path.expanduser().resolve(strict=True)
    receipt = path.with_name(path.name.removesuffix(".tar.gz") + ".archive.json")
    if receipt.is_symlink():
        raise ValueError("archive receipt must not be a symlink")
    manifest = json.loads(receipt.read_text())
    _verify_archive(path, manifest)
    marker_entry = next((item for item in manifest["entries"]
                         if item["path"] == NO_INDEX_MARKER), None)
    if marker_entry is not None and marker_entry["kind"] != "file":
        raise ValueError("archived Kindex exclusion marker must be a regular file")
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("restore destination must not exist")
    if not apply:
        return {"destination": str(destination), "applied": False,
                "kindex_exclusion_marker_added": marker_entry is None}
    destination.mkdir(parents=False, mode=0o700)
    try:
        # Protect even legacy restores before any repository bytes appear. An
        # archived marker is restored first with its original bytes and mode.
        if marker_entry is None:
            mark_no_index(destination)
        ordered_entries = sorted(manifest["entries"],
                                 key=lambda item: item["path"] != NO_INDEX_MARKER)
        # Manual restore never traverses a stored symlink. Links are created last.
        with tarfile.open(path, "r:gz") as archive:
            for item in ordered_entries:
                output = destination / item["path"]
                if item["kind"] == "directory":
                    output.mkdir(parents=True, exist_ok=True)
                elif item["kind"] == "file":
                    output.parent.mkdir(parents=True, exist_ok=True)
                    stream = archive.extractfile(f"{manifest['run']}/{item['path']}")
                    if stream is None:
                        raise ValueError("archive content unavailable")
                    with output.open("xb") as target:
                        shutil.copyfileobj(stream, target)
            for item in manifest["entries"]:
                output = destination / item["path"]
                if item["kind"] == "symlink":
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.symlink_to(item["target"])
        limitations = []
        for item in manifest["entries"]:
            failed = _restore_xattrs(destination / item["path"], item["xattrs"])
            if failed:
                limitations.append({"path": item["path"], "attributes": failed})
        failed = _restore_xattrs(destination, manifest["root_xattrs"])
        if failed:
            limitations.append({"path": ".", "attributes": failed})
        for item in reversed(manifest["entries"]):
            if item["kind"] != "symlink":
                (destination / item["path"]).chmod(item["mode"])
        destination.chmod(manifest["root_mode"])
        # OS-protected attributes may not be settable; originals remain in both
        # the archive and private receipt. Content/modes must still match exactly.
        def content_only(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{key: value for key, value in item.items() if key != "xattrs"}
                    for item in items]
        restored_entries = _entries(destination)
        if marker_entry is None:
            restored_entries = [item for item in restored_entries
                                if item["path"] != NO_INDEX_MARKER]
        if content_only(restored_entries) != content_only(manifest["entries"]):
            raise ValueError("restored files differ from receipt")
    except Exception:
        # Preserve partial output for diagnosis; archive remains authoritative.
        raise
    return {"destination": str(destination), "applied": True,
            "kindex_exclusion_marker_added": marker_entry is None,
            "metadata_restore_limitations": limitations,
            "metadata_retained_in": str(receipt)}
