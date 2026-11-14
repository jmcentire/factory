"""Independent OS primitive controls. NOT hosted custody/Signet qualification.

Oracle: orchestrator's public hosted-kernel acceptance G0,K1-K4 (2026-11-14).
No provider calls, no implementation inspection, all files synthetic.
"""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from harness.validator_boundary import Boundary
from tests.test_native_claude_contract import execute, launch

pytest_plugins = ["tests.test_native_claude_contract"]
H = Path(__file__).resolve().parents[1] / "harness"


@pytest.fixture
def primitive(tmp_path):
    root = tmp_path.resolve()
    for name in ["workspace", "denied", "control", "session-store"]:
        (root / name).mkdir()
    (root / "denied/oracle").write_text("SYNTHETIC_ORACLE_DO_NOT_READ")
    (root / "control/record").write_text("synthetic original control")
    boundary = Boundary.parse(
        {
            "schema": "factory-seat-boundary/1",
            "role": "coder",
            "denied_reads": [str(root / "denied")],
            "protected_writes": [str(root / "control")],
            "allowed_sockets": [],
        }
    )
    contract = root / "control/boundary.json"
    contract.write_text(json.dumps(boundary.document()))
    base = [
        sys.executable,
        str(H / "validator_boundary.py"),
        "--contract",
        str(contract),
        "--digest",
        boundary.digest,
        "--expected-role",
        "coder",
        "--",
    ]
    return root, contract, boundary.digest, base


@pytest.mark.parametrize("agent", ["agy", "muse"])
@pytest.mark.parametrize("require_signet", [False, True])
def test_g0_protected_hosted_wrapper_refuses_before_provider(
    native_case, primitive, tmp_path, agent, require_signet
):
    root, contract, digest, _ = primitive
    case = native_case
    allowed = {
        "HOME",
        "PATH",
        "SHELL",
        "TMPDIR",
        "TERM",
        "LANG",
        "CLAUDE_CONFIG_DIR",
        "FACTORY_VALIDATOR_AGENT",
        "FACTORY_ORCHESTRATOR_AGENT",
        "FACTORY_PYTHON",
        "FACTORY_CONFIG",
        "FACTORY_KINDEX_LITE_BIN",
        "FACTORY_REQUIRE_SIGNET",
        "FACTORY_REQUIRE_AUTHOR_CUSTODY",
        "FACTORY_REQUIRE_BOUNDARY",
    }
    case["env"] = {k: v for k, v in case["env"].items() if k in allowed}
    launch(case)
    if require_signet:
        case["env"]["FACTORY_REQUIRE_SIGNET"] = "1"
    marker = tmp_path / "provider-spawned"
    binary = case["bin_dir"] / agent
    binary.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    binary.chmod(0o755)
    prompt = tmp_path / "brief"
    prompt.write_text("synthetic zero-model admission probe")
    result = execute(
        [
            "bash",
            str(H / "tmux_lane.sh"),
            "native-acceptance",
            "coder",
            "launch",
            "--runs",
            str(case["runs"]),
            "--repo",
            str(root / "workspace"),
            "--prompt",
            str(prompt),
            "--agent",
            agent,
            "--boundary-contract",
            str(contract),
            "--boundary-digest",
            digest,
        ],
        case["env"],
    )
    assert result.returncode == 77, result.stdout + result.stderr
    assert not marker.exists(), "protected hosted gate cannot spawn even preflight"


PROBE = r"""
import errno,json,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1]); result={}
def denied(name,operation):
    try: operation();result[name]=False
    except OSError as e: result[name]=e.errno in (errno.EPERM,errno.EACCES)
denied('read',lambda:(root/'denied/oracle').read_text())
denied('list',lambda:list((root/'denied').iterdir()))
def shell(name,command):
    p=subprocess.run(['/bin/sh','-c',command,'probe',str(root)],capture_output=True,text=True)
    denied_text = 'permitted' in p.stderr.lower() or 'denied' in p.stderr.lower()
    result[name] = p.returncode != 0 and denied_text
    result[name+'_sentinel_absent']='SYNTHETIC_ORACLE_DO_NOT_READ' not in p.stdout+p.stderr
shell('shell_read','cat "$1/denied/oracle"')
denied('overwrite',lambda:(root/'control/record').write_text('changed'))
denied('create',lambda:(root/'control/injected').write_text('changed'))
denied('rename',lambda:(root/'control/record').rename(root/'control/moved'))
denied('unlink',lambda:(root/'control/record').unlink())
shell('shell_write','echo changed > "$1/control/record"')
shell('shell_create','echo changed > "$1/control/injected"')
shell('shell_rename','mv "$1/control/record" "$1/control/moved"')
shell('shell_unlink','rm "$1/control/record"')
(root/'workspace/result').write_text('ordinary output')
(root/'session-store/session.json').write_text(json.dumps({'id':'kernel-synthetic-id','history':['first']}))
result['workspace_write']=(root/'workspace/result').read_text()=='ordinary output'
result['session_write']=json.loads((root/'session-store/session.json').read_text())['id']=='kernel-synthetic-id'
print(json.dumps(result))
"""


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual macOS kernel primitive")
def test_k1_k2_kernel_denies_inprocess_and_shell_but_allows_session_state(primitive):
    root, _, _, base = primitive
    # Sensitivity control: identical interpreter and shell can read outside boundary.
    plain = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pathlib,subprocess,sys; p=pathlib.Path(sys.argv[1]); "
            "assert p.read_text()=='SYNTHETIC_ORACLE_DO_NOT_READ'; "
            "assert subprocess.run(['/bin/cat',str(p)],capture_output=True).returncode==0",
            str(root / "denied/oracle"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert plain.returncode == 0, plain.stderr
    original = hashlib.sha256((root / "control/record").read_bytes()).hexdigest()
    result = subprocess.run(
        base + [sys.executable, "-c", PROBE, str(root)],
        cwd=root / "workspace",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed and all(observed.values()), observed
    assert "SYNTHETIC_ORACLE_DO_NOT_READ" not in result.stdout + result.stderr
    assert hashlib.sha256((root / "control/record").read_bytes()).hexdigest() == original
    assert not (root / "control/injected").exists()
    assert not (root / "control/moved").exists()
    # K3: a fresh guarded process reopens exactly the synthetic persisted session.
    resume = subprocess.run(
        base
        + [
            sys.executable,
            "-c",
            "import json,pathlib,sys; "
            "print(json.dumps(json.loads(pathlib.Path(sys.argv[1]).read_text())))",
            str(root / "session-store/session.json"),
        ],
        cwd=root / "workspace",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert resume.returncode == 0, resume.stderr
    assert json.loads(resume.stdout) == {"id": "kernel-synthetic-id", "history": ["first"]}


def test_k4_digest_mismatch_refuses_before_child(primitive):
    root, _, _, base = primitive
    marker = root / "would-have-spawned"
    base[base.index("--digest") + 1] = "0" * 64
    result = subprocess.run(
        base
        + [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual macOS kernel primitive")
def test_kernel_explicit_socket_allowlist_denies_other_local_socket(primitive):
    import socket
    import tempfile

    root, contract, _, base = primitive
    with tempfile.TemporaryDirectory(prefix="f-kernel-", dir="/tmp") as temporary:
        sockets = Path(temporary).resolve()
        allowed, forbidden = sockets / "broker.sock", sockets / "foreign.sock"
        with socket.socket(socket.AF_UNIX) as broker, socket.socket(socket.AF_UNIX) as other:
            broker.bind(str(allowed))
            broker.listen()
            other.bind(str(forbidden))
            other.listen()
            value = json.loads(contract.read_text())
            value["protected_writes"].append(str(sockets))
            value["allowed_sockets"] = [str(allowed)]
            boundary = Boundary.parse(value)
            contract.write_text(json.dumps(boundary.document()))
            base[base.index("--digest") + 1] = boundary.digest
            probe = """import errno,json,socket,sys
out={}
for name,path in [('allowed',sys.argv[1]),('forbidden',sys.argv[2])]:
 with socket.socket(socket.AF_UNIX) as s:
  s.settimeout(2)
  try: s.connect(path);out[name]='connected'
  except OSError as e: out[name]='denied' if e.errno in (errno.EPERM,errno.EACCES) else repr(e)
print(json.dumps(out))
"""
            args = [sys.executable, "-c", probe, str(allowed), str(forbidden)]
            plain = subprocess.run(args, capture_output=True, text=True, timeout=10)
            assert plain.returncode == 0, plain.stderr
            assert json.loads(plain.stdout) == {"allowed": "connected", "forbidden": "connected"}
            guarded = subprocess.run(
                base + args, cwd=root, capture_output=True, text=True, timeout=10
            )
            assert guarded.returncode == 0, guarded.stderr
            assert json.loads(guarded.stdout) == {"allowed": "connected", "forbidden": "denied"}


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual macOS kernel primitive")
@pytest.mark.parametrize("kind", ["fake-global-kindex", "fake-provider-settings"])
def test_kernel_synthetic_global_store_and_settings_immutable(primitive, kind):
    root, contract, _, base = primitive
    store = root / kind
    store.mkdir()
    record = store / ("kindex.db" if kind == "fake-global-kindex" else "settings.json")
    record.write_text("SYNTHETIC_ORIGINAL_NOT_OPERATOR_STATE")
    value = json.loads(contract.read_text())
    value["protected_writes"].append(str(store))
    if kind == "fake-global-kindex":
        value["denied_reads"].append(str(store))
    boundary = Boundary.parse(value)
    contract.write_text(json.dumps(boundary.document()))
    base[base.index("--digest") + 1] = boundary.digest
    original = record.read_bytes()
    probe = """import errno,json,pathlib,subprocess,sys
p=pathlib.Path(sys.argv[1]);out={}
def attempt(name,fn):
 try: fn();out[name]=False
 except OSError as e:out[name]=e.errno in (errno.EPERM,errno.EACCES)
if sys.argv[2]=='fake-global-kindex':
 attempt('read',lambda:p.read_text())
 attempt('list',lambda:list(p.parent.iterdir()))
 c=subprocess.run(['/bin/sh','-c','cat "$1"','probe',str(p)],capture_output=True,text=True)
 out['shell_read']=c.returncode!=0 and 'SYNTHETIC_ORIGINAL' not in c.stdout
attempt('overwrite',lambda:p.write_text('changed'))
attempt('create',lambda:(p.parent/'injected').write_text('changed'))
attempt('rename',lambda:p.rename(p.parent/'moved'))
attempt('unlink',lambda:p.unlink())
for name,script in [('shell_overwrite','echo changed > "$1"'),
                    ('shell_create','echo changed > "$2/injected"'),
                    ('shell_rename','mv "$1" "$2/moved"'),('shell_unlink','rm "$1"')]:
 c=subprocess.run(['/bin/sh','-c',script,'probe',str(p),str(p.parent)],capture_output=True,text=True)
 out[name]=c.returncode!=0 and ('denied' in c.stderr.lower() or 'permitted' in c.stderr.lower())
print(json.dumps(out))
"""
    # Outside the boundary the synthetic store is genuinely writable/readable.
    record.write_bytes(original)
    assert record.read_bytes() == original
    result = subprocess.run(
        base + [sys.executable, "-c", probe, str(record), kind],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout).values()), result.stdout
    assert record.read_bytes() == original
    assert not (store / "injected").exists() and not (store / "moved").exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual macOS kernel primitive")
def test_kernel_protects_code_ancestors_and_parent_signal_access(primitive):
    import os

    root, contract, _, base = primitive
    trusted = root / "trusted-parent"
    code = trusted / "code"
    code.mkdir(parents=True)
    module = code / "synthetic_host.py"
    module.write_text("ORIGINAL_SYNTHETIC_TRUSTED_CODE")
    value = json.loads(contract.read_text())
    value["protected_writes"].append(str(code))
    boundary = Boundary.parse(value)
    contract.write_text(json.dumps(boundary.document()))
    base[base.index("--digest") + 1] = boundary.digest
    probe = r"""
import errno,fcntl,json,os,pathlib,stat,subprocess,sys
root=pathlib.Path(sys.argv[1]);parent=int(sys.argv[2]);out={}
def deny(name,fn):
 try:fn();out[name]=False
 except OSError as e:out[name]=e.errno in (errno.EPERM,errno.EACCES)
deny('code_write',lambda:(root/'trusted-parent/code/synthetic_host.py').write_text('forged'))
deny('ancestor_rename',lambda:(root/'trusted-parent').rename(root/'moved-parent'))
deny('code_unlink',lambda:(root/'trusted-parent/code/synthetic_host.py').unlink())
deny('parent_signal_access',lambda:os.kill(parent,0))
c=subprocess.run(['/bin/sh','-c','kill -0 "$1"','probe',str(parent)],capture_output=True,text=True)
out['shell_signal_access']=c.returncode!=0
# Pipes/stdin/stdout are expected; writable regular file handles beyond stdio are not.
unsafe=[]
for fd in range(3,128):
 try:
  if stat.S_ISREG(os.fstat(fd).st_mode) and fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY:
   unsafe.append(fd)
 except OSError:pass
out['no_writable_regular_inherited_fd']=not unsafe
print(json.dumps(out))
"""
    result = subprocess.run(
        base + [sys.executable, "-c", probe, str(root), str(os.getpid())],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout).values()), result.stdout
    assert module.read_text() == "ORIGINAL_SYNTHETIC_TRUSTED_CODE"
    assert not (root / "moved-parent").exists()


@pytest.mark.skipif(platform.system() != "Darwin", reason="actual macOS kernel primitive")
def test_kernel_entry_does_not_leak_preopened_protected_descriptors(primitive):
    import os

    root, _, _, base = primitive
    original = (root / "control/record").read_bytes()
    read_fd = os.open(root / "denied/oracle", os.O_RDONLY)
    write_fd = os.open(root / "control/record", os.O_WRONLY)
    try:
        probe = """import errno,json,os,sys
out={}
for name,fd in [('read',int(sys.argv[1])),('write',int(sys.argv[2]))]:
 try:
  os.read(fd,1024) if name=='read' else os.write(fd,b'forged')
  out[name]=False
 except OSError as e:out[name]=e.errno in (errno.EBADF,errno.EPERM,errno.EACCES)
print(json.dumps(out))
"""
        result = subprocess.run(
            base + [sys.executable, "-c", probe, str(read_fd), str(write_fd)],
            pass_fds=(read_fd, write_fd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"read": True, "write": True}
        assert (root / "control/record").read_bytes() == original
    finally:
        os.close(read_fd)
        os.close(write_fd)
