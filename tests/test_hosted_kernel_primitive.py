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
def test_g0_protected_hosted_wrapper_refuses_before_provider(
    native_case, primitive, tmp_path, agent
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
