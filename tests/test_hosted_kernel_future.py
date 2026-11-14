"""Future kernel-only hosted admission, intentionally red on PR117 baseline.

Static kernel only; semantic Signet/custody requests remain separately refused.
No implementation inspection, no actual providers, no native sandbox override.
"""

import json
import sys
import time
from pathlib import Path

import pytest

from harness.validator_boundary import Boundary
from tests.test_hosted_transport_contract import FAKE
from tests.test_native_claude_contract import execute, launch

pytest_plugins = ["tests.test_native_claude_contract"]
H = Path(__file__).resolve().parents[1] / "harness"


def eventually(check):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    raise AssertionError("public hosted kernel lifecycle outcome missing")


@pytest.mark.parametrize("agent", ["agy", "muse"])
@pytest.mark.parametrize("tamper", [False, True])
def test_kernel_only_hosted_launch_live_message_and_retained_restart(
    native_case, tmp_path, agent, tamper
):
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
    run = case["runs"] / "native-acceptance"
    denied = tmp_path / "withheld"
    denied.mkdir()
    (denied / "oracle").write_text("synthetic private oracle")
    control = tmp_path / "protected-control"
    control.mkdir()
    record = control / "record"
    record.write_text("original")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    boundary = Boundary.parse(
        {
            "schema": "factory-seat-boundary/1",
            "role": "coder",
            "denied_reads": [str(denied.resolve())],
            "protected_writes": [str(control.resolve()), str(run.resolve())],
            "allowed_sockets": [],
        }
    )
    contract = control / "boundary.json"
    contract.write_text(json.dumps(boundary.document()))
    provider = case["bin_dir"] / agent
    # Public captured built-in account status shapes; deterministic and no network.
    auth = """
if args == ['models']:
    print('gemini-4-carbon-medium\\tGemini 4 Carbon (Medium)'); sys.exit()
if args and args[0] == 'usage':
    print(json.dumps({'schemaVersion':1,'allowance':None,'reason':'server_sent_no_usage'}));sys.exit()
"""
    body = FAKE.replace('assert not {"-p",', auth + '\nassert not {"-p",')
    # Provider must not scan trusted-host receipts. The independent probe asks the
    # kernel directly and logs only denial booleans outside the protected run.
    start = body.index("        snapshots = {")
    end = body.index("        log(prompt=p, durable_before_write=snapshots)", start)
    body = body[:start] + "        snapshots = {}\n" + body[end:]
    probe = f"""
        import errno,subprocess
        result={{}}
        for name,path,mode in [('read',{str(denied / "oracle")!r},'r'),
                              ('control',{str(record)!r},'w'),
                              ('host_state',{str(run / "tmux-lanes/coder-persistent.json")!r},'w')]:
            try:
                with open(path,mode) as stream:
                    stream.read() if mode=='r' else stream.write('forged')
                result[name]=False
            except OSError as error:
                result[name]=error.errno in (errno.EACCES,errno.EPERM)
        child=subprocess.run(['/bin/cat',{str(denied / "oracle")!r}],capture_output=True,text=True)
        result['child_read']=child.returncode!=0 and 'synthetic private oracle' not in child.stdout
        log(kernel_probe=result)
"""
    body = body.replace(
        "        log(prompt=p, durable_before_write=snapshots)",
        probe + "\n        log(prompt=p, durable_before_write=snapshots)",
    )
    provider.write_text(f"#!{sys.executable}\n" + body)
    provider.chmod(0o755)
    prompt = tmp_path / "brief"
    prompt.write_text("synthetic initial kernel-only hosted message")
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
            str(workspace),
            "--prompt",
            str(prompt),
            "--agent",
            agent,
            "--boundary-contract",
            str(contract),
            "--boundary-digest",
            boundary.digest,
        ],
        case["env"],
    )
    assert result.returncode == 0, result.stdout + result.stderr

    def rows():
        path = case["bin_dir"] / "rpc.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def state():
        path = run / "tmux-lanes/coder-persistent.json"
        return json.loads(path.read_text()) if path.exists() else {}

    eventually(lambda: state().get("deliveries", {}).get("initial", {}).get("response_stop"))
    sid = state()["session_id"]

    def message():
        return execute(
            [
                "bash",
                str(H / "tmux_lane_message.sh"),
                "native-acceptance",
                "validator",
                "coder",
                "status",
                "--runs",
                str(case["runs"]),
            ],
            case["env"],
        )

    (case["bin_dir"] / "exit-after-completion").touch()
    live = message()
    assert live.returncode in (0, 75), live.stdout + live.stderr
    eventually(lambda: len([r for r in rows() if "prompt" in r]) == 2)
    eventually(
        lambda: (
            any((run / "tmux-lanes/coder-exits.jsonl").read_text().splitlines())
            if (run / "tmux-lanes/coder-exits.jsonl").exists()
            else False
        )
    )
    before = len([r for r in rows() if r.get("spawn")])
    if tamper:
        changed = json.loads(contract.read_text())
        changed["protected_writes"].append(str((tmp_path / "new-protected-root").resolve()))
        contract.write_text(json.dumps(changed))
    resumed = message()
    if tamper:
        assert resumed.returncode == 70, resumed.stdout + resumed.stderr
        assert len([r for r in rows() if r.get("spawn")]) == before
    else:
        assert resumed.returncode in (0, 75), resumed.stdout + resumed.stderr
        eventually(lambda: len([r for r in rows() if "prompt" in r]) == 3)
        assert state()["session_id"] == sid
        assert any(r.get("request", {}).get("method") == "session/resume" for r in rows())
    probes = [r["kernel_probe"] for r in rows() if "kernel_probe" in r]
    assert len(probes) == (2 if tamper else 3)
    assert all(all(p.values()) for p in probes), probes
    assert record.read_text() == "original"
