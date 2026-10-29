"""Boundary tests use only disposable synthetic files; never operator history or credentials."""
from __future__ import annotations

import json
import os
import pathlib
import platform
import socket
import subprocess
import sys

import pytest

from harness.validator_boundary import Boundary, BoundaryError, seat_environment


def contract(tmp_path):
    return {"schema": "factory-seat-boundary/1", "role": "validator",
            "denied_reads": [str(tmp_path / "private" / "author")],
            "protected_writes": [str(tmp_path / "control")], "allowed_sockets": []}


@pytest.mark.parametrize("change", [
    {"schema": "unknown"}, {"role": "admin"}, {"role": []}, {"denied_reads": []},
    {"protected_writes": []}, {"denied_reads": ["relative"]},
    {"denied_reads": ["/"]}, {"denied_reads": ["/tmp/../private"]},
    {"denied_reads": ["/tmp/line\nbreak"]}, {"denied_reads": "not-an-array"},
    {"unexpected": True}, {"allowed_sockets": ["/tmp/unprotected.sock"]},
])
def test_invalid_boundary_fails_closed(tmp_path, change):
    with pytest.raises(BoundaryError):
        Boundary.parse({**contract(tmp_path), **change})


def test_boundary_digest_binds_role_and_paths(tmp_path):
    value = contract(tmp_path)
    boundary = Boundary.parse(value)
    assert Boundary.parse(boundary.document()) == boundary
    assert boundary.digest == Boundary.parse(value).digest
    # The role belongs in the admitted contract even when the kernel path sets coincide.
    changed = Boundary.parse({**value, "denied_reads": [str(tmp_path / "different")]})
    assert changed.digest != boundary.digest
    assert Boundary.parse({**value, "role": "tester"}).digest != boundary.digest


def test_environment_preserves_terminal_and_plan_paths_without_ambient_credentials():
    original = {"HOME": "/fixture/home", "PATH": "/fixture/bin", "CODEX_HOME": "/fixture/codex",
                "KIN_PROJECT_PATH": "/fixture/repo", "BASH_FUNC_cl%%": "() { claude; }",
                "ANTHROPIC_API_KEY": "synthetic-key", "NEW_UNRECOGNIZED_SECRET": "synthetic",
                "CRATES_IO_FOR_SIGNET_EVAL": "synthetic", "HTTP_PROXY": "synthetic"}
    clean = seat_environment(original)
    assert set(clean) == {"HOME", "PATH", "CODEX_HOME", "KIN_PROJECT_PATH", "BASH_FUNC_cl%%"}
    assert original["ANTHROPIC_API_KEY"] == "synthetic-key"  # parent/persisted env untouched


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS DNS service")
def test_system_dns_socket_remains_reachable_without_allowing_general_host_ipc(tmp_path):
    boundary = Boundary.parse(contract(tmp_path))
    command = [sys.executable, "-c",
        "import socket; s=socket.socket(socket.AF_UNIX); s.settimeout(2); "
        "s.connect('/private/var/run/mDNSResponder'); s.close()"]
    ran = subprocess.run(boundary.invocation(command, cwd=tmp_path),
                         capture_output=True, text=True, timeout=5)
    assert ran.returncode == 0, ran.stderr


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS Seatbelt qualification")
def test_kernel_blocks_interpreter_alias_and_parent_rename_but_keeps_useful_access(tmp_path):
    private = tmp_path / "private"
    author = private / "author"
    author.mkdir(parents=True)
    (author / "content").write_text("synthetic author data")
    control = tmp_path / "control"
    control.mkdir()
    (control / "receipt").write_text("host-owned")
    alias = tmp_path / "alias"
    alias.symlink_to(author, target_is_directory=True)
    public = tmp_path / "shared"
    public.write_text("permitted shared contract")
    # Owner correction 2026-10-28: ordinary peer runtime links must not prevent
    # startup; direct and aliased reads of peer work must still be denied.
    (author / "internal-link").symlink_to("content")
    (author / "shared-link").symlink_to(public)
    (author / "dangling-link").symlink_to("absent-runtime")
    (author / "cyclic-link").symlink_to("cyclic-link")
    boundary = Boundary.parse(contract(tmp_path))
    script = '''import json,pathlib,sys
root=pathlib.Path(sys.argv[1]); result={}
for name,path in [('direct',root/'private/author/content'),('alias',root/'alias/content'),
                  ('internal',root/'alias/internal-link')]:
 try: path.read_text(); result[name]=False
 except PermissionError: result[name]=True
try: (root/'new-hardlink').hardlink_to(root/'private/author/content'); result['hardlink']=False
except PermissionError: result['hardlink']=True
try: (root/'control/receipt').write_text('forged'); result['control_write']=False
except PermissionError: result['control_write']=True
try: (root/'private').rename(root/'moved'); result['parent_rename']=False
except PermissionError: result['parent_rename']=True
result['shared_read']=(root/'shared').read_text()=='permitted shared contract'
(root/'ordinary-output').write_text('ordinary work')
result['ordinary_write']=(root/'ordinary-output').read_text()=='ordinary work'
print(json.dumps(result))
'''
    ran = subprocess.run(boundary.invocation([sys.executable, "-c", script, str(tmp_path)],
                                            cwd=tmp_path), cwd=tmp_path,
                         capture_output=True, text=True, timeout=15)
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout) == dict.fromkeys(
        ["direct", "alias", "internal", "hardlink", "control_write", "parent_rename", "shared_read",
         "ordinary_write"],
        True)
    assert (control / "receipt").read_text() == "host-owned"


def test_admission_rejects_existing_hardlink_without_reading_contents(tmp_path):
    private = tmp_path / "private" / "author"
    private.mkdir(parents=True)
    data = private / "content"
    data.write_text("synthetic")
    (tmp_path / "outside").hardlink_to(data)
    with pytest.raises(BoundaryError):
        Boundary.parse(contract(tmp_path)).check_topology()


def shared_runtime(tmp_path):
    from harness.agents import _keychain_home
    peer = tmp_path / "private/author"
    operator = tmp_path / "operator"
    (operator / "Library").mkdir(parents=True)
    home = _keychain_home(peer / ".git/factory-runtime/tester/agent", operator)
    link = home / "Library"
    value = contract(tmp_path)
    value["shared_read_aliases"] = {str(link): str(operator / "Library")}
    return value, link, peer, operator / "Library"


def test_existing_private_login_home_link_does_not_change_kernel_permissions(tmp_path):
    value, link, _, target = shared_runtime(tmp_path)
    Boundary.parse(contract(tmp_path)).check_topology()
    boundary = Boundary.parse(value)
    boundary.check_topology()
    assert boundary.document()["shared_read_aliases"] == {str(link): str(target)}
    assert Boundary.parse(boundary.document()).digest == boundary.digest
    # A declaration changes the admission digest, not the kernel read/write policy.
    link.unlink()
    plain = Boundary.parse(contract(tmp_path))
    assert boundary.profile() == plain.profile()
    assert boundary.digest != plain.digest


@pytest.mark.parametrize("change", ["retarget", "regular"])
def test_shared_runtime_does_not_admit_changed_declared_links(tmp_path, change):
    value, link, peer, _ = shared_runtime(tmp_path)
    boundary = Boundary.parse(value)
    link.unlink()
    if change == "retarget":
        link.symlink_to(tmp_path / "elsewhere")
    else:
        link.mkdir()
    with pytest.raises(BoundaryError):
        boundary.check_topology()


@pytest.mark.parametrize("change", ["outside", "target-peer", "target-parent", "not-mapping"])
def test_shared_runtime_mapping_cannot_declare_peer_content_shared(tmp_path, change):
    value, link, peer, target = shared_runtime(tmp_path)
    destination = peer if change == "target-peer" else (
        tmp_path if change == "target-parent" else target)
    value["shared_read_aliases"] = (
        [] if change == "not-mapping" else
        {str(tmp_path / "outside" if change == "outside" else link):
         str(destination)}
    )
    with pytest.raises(BoundaryError):
        Boundary.parse(value)


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual private-home kernel boundary")
def test_shared_login_alias_keeps_peer_content_and_link_mutation_denied(tmp_path):
    value, link, peer, shared = shared_runtime(tmp_path)
    (peer / "implementation").write_text("synthetic private work")
    (shared / "login-fixture").write_text("synthetic shared runtime")
    outside = tmp_path / "outside-peer-alias"
    outside.symlink_to(peer)
    script = '''import pathlib,sys
peer,link,shared,outside=map(pathlib.Path,sys.argv[1:])
for path in (peer/'implementation',outside/'implementation'):
 try: path.read_text(); sys.exit(41)
 except PermissionError: pass
try: link.unlink(); sys.exit(42)
except PermissionError: pass
assert (shared/'login-fixture').read_text()=='synthetic shared runtime'
'''
    boundary = Boundary.parse(value)
    ran = subprocess.run(boundary.invocation(
        [sys.executable, "-c", script, str(peer), str(link), str(shared), str(outside)],
        cwd=tmp_path), capture_output=True, text=True, timeout=10)
    assert ran.returncode == 0, ran.stderr
    assert link.is_symlink() and (peer / "implementation").read_text() == "synthetic private work"


@pytest.mark.parametrize("protected_target", [False, True])
def test_runtime_alias_requires_separately_protected_target(tmp_path, protected_target):
    control = tmp_path / "control"
    control.mkdir()
    runtime = tmp_path / "runtime"
    runtime.write_text("synthetic executable")
    (control / "python").symlink_to(runtime)
    value = contract(tmp_path)
    if protected_target:
        value["protected_writes"].append(str(runtime))
        Boundary.parse(value).check_topology()
    else:
        with pytest.raises(BoundaryError, match="outside the boundary"):
            Boundary.parse(value).check_topology()


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS alias protection")
def test_kernel_protects_both_runtime_alias_and_its_declared_target(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    runtime = tmp_path / "runtime"
    runtime.write_text("synthetic immutable runtime")
    alias = control / "python"
    alias.symlink_to(runtime)
    value = contract(tmp_path)
    value["protected_writes"].append(str(runtime))
    boundary = Boundary.parse(value)
    script = '''import pathlib,sys
alias,target=map(pathlib.Path,sys.argv[1:])
operations=(alias.unlink,lambda:target.write_text('changed'),lambda:alias.write_text('changed'))
for operation in operations:
 try: operation(); sys.exit(42)
 except PermissionError: pass
'''
    ran = subprocess.run(boundary.invocation(
        [sys.executable, "-c", script, str(alias), str(runtime)], cwd=tmp_path),
        capture_output=True, text=True, timeout=5)
    assert ran.returncode == 0, ran.stderr
    assert alias.is_symlink() and runtime.read_text() == "synthetic immutable runtime"


@pytest.mark.parametrize("kind", ["missing", "cyclic"])
def test_unresolved_runtime_alias_is_refused(tmp_path, kind):
    control = tmp_path / "control"
    control.mkdir()
    alias = control / "alias"
    alias.symlink_to(alias if kind == "cyclic" else control / "missing")
    with pytest.raises(BoundaryError, match="cannot be resolved"):
        Boundary.parse(contract(tmp_path)).check_topology()


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS alias protection")
def test_intermediate_runtime_directory_alias_cannot_be_retargeted(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "python").write_text("synthetic executable")
    intermediate = tmp_path / "package-manager-alias"
    intermediate.symlink_to(runtime, target_is_directory=True)
    (control / "python").symlink_to(intermediate / "python")
    value = contract(tmp_path)
    value["protected_writes"].append(str(runtime))
    boundary = Boundary.parse(value)
    assert intermediate in boundary.check_topology()
    script = '''import pathlib,sys
link=pathlib.Path(sys.argv[1])
try: link.unlink(); sys.exit(42)
except PermissionError: pass
'''
    ran = subprocess.run(boundary.invocation(
        [sys.executable, "-c", script, str(intermediate)], cwd=tmp_path),
        capture_output=True, text=True, timeout=5)
    assert ran.returncode == 0, ran.stderr
    assert intermediate.is_symlink()


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS Seatbelt qualification")
def test_sibling_environment_is_not_exposed(tmp_path):
    # Inspect only this test-owned process, started with a closed entirely synthetic env.
    child = subprocess.Popen(["/bin/sleep", "10"], env={"FACTORY_TEST_SECRET": "synthetic-only"})
    try:
        boundary = Boundary.parse(contract(tmp_path))
        inspected = subprocess.run(boundary.invocation(
            ["/bin/ps", "eww", "-p", str(child.pid)], cwd=tmp_path),
            capture_output=True, text=True, timeout=5)
        assert "FACTORY_TEST_SECRET" not in inspected.stdout
        assert "synthetic-only" not in inspected.stdout
    finally:
        child.terminate()
        child.wait(timeout=3)


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS Seatbelt qualification")
def test_cli_closes_inherited_host_file_descriptors(tmp_path):
    import harness.validator_boundary as module
    control = tmp_path / "control"
    control.mkdir()
    boundary = Boundary.parse(contract(tmp_path))
    config = control / "boundary.json"
    config.write_text(json.dumps(boundary.document()))
    synthetic = tmp_path / "preopened"
    synthetic.write_text("host-only synthetic bytes")
    fd = os.open(synthetic, os.O_RDONLY)
    try:
        script = ("import os,sys;\ntry: os.read(int(sys.argv[1]),100)\n"
                  "except OSError: sys.exit(0)\nsys.exit(1)")
        ran = subprocess.run([sys.executable, module.__file__, "--contract", str(config),
            "--digest", boundary.digest, "--", sys.executable, "-c", script, str(fd)],
            cwd=tmp_path, capture_output=True, text=True, pass_fds=(fd,), timeout=5)
        assert ran.returncode == 0, ran.stderr
    finally:
        os.close(fd)


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS Seatbelt qualification")
def test_seat_cannot_signal_host_but_can_manage_its_own_child(tmp_path):
    sibling = subprocess.Popen(["/bin/sleep", "10"])
    try:
        script = '''import os,signal,subprocess,sys,json
out={}
try: os.kill(int(sys.argv[1]),signal.SIGTERM); out['host']='signalled'
except PermissionError: out['host']='denied'
child=subprocess.Popen(['/bin/sleep','10'])
child.terminate(); child.wait(timeout=3)
out['child']='stopped'
print(json.dumps(out))
'''
        boundary = Boundary.parse(contract(tmp_path))
        ran = subprocess.run(boundary.invocation(
            [sys.executable, "-c", script, str(sibling.pid)], cwd=tmp_path),
            capture_output=True, text=True, timeout=5)
        assert ran.returncode == 0, ran.stderr
        assert json.loads(ran.stdout) == {"host": "denied", "child": "stopped"}
        assert sibling.poll() is None
    finally:
        if sibling.poll() is None:
            sibling.terminate()
        sibling.wait(timeout=3)


@pytest.mark.skipif(platform.system() != "Darwin", reason="real macOS Seatbelt qualification")
def test_kernel_denies_host_unix_socket_but_allows_explicit_broker_and_ip(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    # macOS Unix socket addresses have a small length limit; pytest paths can exceed it.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="f-boundary-", dir="/tmp") as directory:
        root = pathlib.Path(directory).resolve()
        broker_path, forbidden_path = root / "broker.sock", root / "other.sock"
        with socket.socket(socket.AF_UNIX) as broker, socket.socket(socket.AF_UNIX) as other, \
                socket.socket(socket.AF_INET) as network:
            broker.bind(str(broker_path))
            broker.listen()
            other.bind(str(forbidden_path))
            other.listen()
            network.bind(("127.0.0.1", 0))
            network.listen()
            value = contract(tmp_path)
            value["protected_writes"].append(str(root))
            value["allowed_sockets"] = [str(broker_path)]
            boundary = Boundary.parse(value)
            script = '''import json,socket,sys
out={}
for key,address in [('forbidden',sys.argv[1]),('broker',sys.argv[2])]:
 with socket.socket(socket.AF_UNIX) as sock:
  sock.settimeout(2)
  try: sock.connect(address); out[key]='connected'
  except PermissionError: out[key]='denied'
with socket.create_connection(('127.0.0.1',int(sys.argv[3])),timeout=2): out['ip']='connected'
print(json.dumps(out))
'''
            ran = subprocess.run(boundary.invocation(
                [sys.executable, "-c", script, str(forbidden_path), str(broker_path),
                 str(network.getsockname()[1])], cwd=tmp_path), cwd=tmp_path,
                capture_output=True, text=True, timeout=15)
            assert ran.returncode == 0, ran.stderr
            assert json.loads(ran.stdout) == {
                "forbidden": "denied", "broker": "connected", "ip": "connected"}
