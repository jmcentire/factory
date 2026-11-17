"""Offline startup smoke: real Go builds, mocked GitHub transport, temporary destinations."""

import os
import platform
import subprocess
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
asset = f"factory-setup-{platform.system().lower()}-{arch}"

with tempfile.TemporaryDirectory(prefix="chiron-installer-smoke-") as tmp:
    root = Path(tmp)
    binary = root / "factory-setup-built"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/factory-setup"], cwd=SOURCE, check=True
    )
    fakebin = root / "tools"
    fakebin.mkdir()
    gh = fakebin / "gh"
    gh.write_text("""#!/usr/bin/env python3
import hashlib, os, shutil, subprocess, sys
from pathlib import Path
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    raise SystemExit(0)
if a[:1] == ["api"]:
    print("v0.0.0-smoke")
    raise SystemExit(0)
if a[:2] == ["release", "download"]:
    assert a[a.index("--repo") + 1] == "wandercom/chiron", a
    if os.environ["SMOKE_MODE"] in ("source", "forced-source"):
        if os.environ["SMOKE_MODE"] == "forced-source":
            raise SystemExit("forced source unexpectedly downloaded a release")
        raise SystemExit(1)
    d = Path(a[a.index("--dir") + 1])
    name = a[a.index("--pattern") + 1]
    shutil.copyfile(os.environ["SMOKE_BINARY"], d / name)
    checksum = hashlib.sha256((d / name).read_bytes()).hexdigest()
    if os.environ["SMOKE_MODE"] == "bad-checksum":
        checksum = "0" * 64
    (d / "checksums.txt").write_text(checksum + "  " + name + "\\n")
    raise SystemExit(0)
if a[:2] == ["repo", "clone"]:
    assert a[2] == "wandercom/chiron", a
    shutil.copytree(os.environ["SMOKE_SOURCE"], a[3],
        ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__"))
    raise SystemExit(0)
raise SystemExit("Unexpected gh invocation: " + repr(a))
""")
    gh.chmod(0o755)
    for mode in ("release", "source", "forced-source", "bad-checksum"):
        destination = root / mode / "bin"
        env = dict(
            os.environ,
            PATH=str(fakebin) + os.pathsep + os.environ["PATH"],
            FACTORY_SETUP_BIN_DIR=str(destination),
            SMOKE_MODE=mode,
            SMOKE_SOURCE=str(SOURCE),
            SMOKE_BINARY=str(binary),
            FACTORY_SETUP_FORCE_SOURCE="1" if mode == "forced-source" else "0",
        )
        env.pop("FACTORY_SETUP_REPO", None)
        env.pop("FACTORY_SETUP_REF", None)
        env.pop("FACTORY_SETUP_VERSION", None)
        result = subprocess.run(
            ["bash", str(SOURCE / "install.sh"), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
        )
        if mode == "bad-checksum":
            assert result.returncode != 0 and "checksum mismatch" in result.stderr, result.stderr
            assert not (destination / "factory-setup").exists()
        else:
            assert result.returncode == 0, result.stderr
            assert (destination / "factory-setup").is_file()
            assert "dev" in result.stdout + result.stderr
        print(f"{mode}: PASS")
