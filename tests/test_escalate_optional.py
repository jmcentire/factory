"""Independent black-box regressions for optional Herald reporting.

Only public reporting/CLI contracts are exercised. Credentials, run roots, and
HTTP responses are synthetic; no live service or Factory process is used.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from tests.test_escalate import FakeHerald, client, escalate, run_root

CLI = Path(__file__).resolve().parents[1] / "harness" / "escalate.py"
OPERATIONS = ("post", "question", "heartbeat", "resolve")


@pytest.fixture(autouse=True)
def isolated_reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FACTORY_HERALD", "HERALD_FACTORY_TOKEN", "HERALD_URL"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    def forbidden_network(*_args, **_kwargs):
        pytest.fail("A reporting test attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)


def post(root: Path, **overrides) -> dict:
    arguments = {
        "level": "important",
        "key": "decision",
        "summary": "Choose a deployment window",
        "detail": "Retain this local evidence",
    }
    arguments.update(overrides)
    return escalate.escalate(root, **arguments)


def invoke(root: Path, operation: str, **kwargs) -> dict:
    if operation in ("post", "question"):
        return post(root, question=operation == "question", **kwargs)
    if operation == "heartbeat":
        return escalate.heartbeat(root, interval=432, done=True, **kwargs)
    return escalate.resolve(root, key="decision", **kwargs)


def assert_retained(root: Path, result: dict) -> dict:
    assert result["recorded"] is True and result["delivered"] is False
    records = [
        json.loads(line)
        for line in (root / "orchestrator" / "escalations.jsonl").read_text().splitlines()
    ]
    record = records[-1]
    assert record["delivered"] is False
    assert record["delivery"] == result["delivery"]
    assert record["delivery"] in {"disabled", "not-configured", "local-only", "failed"}
    return record


@pytest.mark.parametrize("mode", [None, "auto", "off"])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_unconfigured_or_disabled_reporting_retains_local_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None, operation: str
) -> None:
    root = run_root(tmp_path)
    if mode is not None:
        monkeypatch.setenv("FACTORY_HERALD", mode)
    if operation == "resolve":
        post(root)
    record = assert_retained(root, invoke(root, operation))
    if operation in ("post", "question"):
        assert record["summary"] == "Choose a deployment window"
        assert record["detail"] == "Retain this local evidence"
    elif operation == "heartbeat":
        assert record["interval_seconds"] == 432 and record["done"] is True


@pytest.mark.parametrize("operation", OPERATIONS)
def test_off_does_not_call_even_an_explicit_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root, endpoint = run_root(tmp_path), FakeHerald()
    if operation == "resolve":
        post(root, client=client(FakeHerald()))
    monkeypatch.setenv("FACTORY_HERALD", "off")
    assert_retained(root, invoke(root, operation, client=client(endpoint)))
    assert endpoint.messages == []


class HTTPResponse(io.BytesIO):
    def __init__(self, status: int, headers: dict, body: bytes) -> None:
        super().__init__(body)
        self.status, self.headers = status, headers

    def getcode(self) -> int:
        return self.status


@pytest.mark.parametrize("mode", [None, "auto", "off"])
@pytest.mark.parametrize("source", ["environment", "saved-file"])
def test_existing_credentials_and_endpoint_configuration_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None, source: str
) -> None:
    root, endpoint = run_root(tmp_path), FakeHerald()
    if mode is not None:
        monkeypatch.setenv("FACTORY_HERALD", mode)
    if source == "environment":
        monkeypatch.setenv("HERALD_FACTORY_TOKEN", "synthetic-token")
        monkeypatch.setenv("HERALD_URL", "https://configured-herald.invalid")
        expected_url = "https://configured-herald.invalid/mcp"
    else:
        token = Path(os.environ["HOME"]) / ".harness/secrets/HERALD_FACTORY_TOKEN"
        token.parent.mkdir(parents=True)
        token.write_text("synthetic-token\n")
        expected_url = "https://dash.herald.tools/mcp"
    urls = []

    def fake_urlopen(request, *_args, **_kwargs):
        urls.append(request.full_url)
        return HTTPResponse(
            *endpoint(request.full_url, dict(request.header_items()), request.data)
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = post(root)
    if mode == "off":
        assert_retained(root, result)
        assert endpoint.messages == []
    else:
        assert result["delivered"] is True
        assert endpoint.calls()[0][0] == "needs_attention"
        assert urls and set(urls) == {expected_url}
        for headers in endpoint.headers:
            assert {k.lower(): v for k, v in headers.items()}["authorization"] == (
                "Bearer synthetic-token"
            )


@pytest.mark.parametrize("mode", ["off", "auto"])
@pytest.mark.parametrize("operation", ["post", "heartbeat", "resolve"])
def test_off_ignores_unreadable_credentials_but_auto_reports_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, operation: str
) -> None:
    root = run_root(tmp_path)
    if operation == "resolve":
        post(root, client=client(FakeHerald()))
    token = Path(os.environ["HOME"]) / ".harness/secrets/HERALD_FACTORY_TOKEN"
    token.parent.mkdir(parents=True)
    token.write_text("synthetic-token")
    original_open = Path.open
    attempts = []

    def unreadable(path, *args, **kwargs):
        if path == token:
            attempts.append(path)
            raise PermissionError("Synthetic unreadable credential file")
        return original_open(path, *args, **kwargs)

    # A deterministic permission failure also works as root and on non-POSIX hosts.
    monkeypatch.setattr(Path, "open", unreadable)
    monkeypatch.setenv("FACTORY_HERALD", mode)
    if mode == "off":
        assert_retained(root, invoke(root, operation))
        assert attempts == []
    else:
        with pytest.raises((OSError, escalate.EscalationError)):
            invoke(root, operation)
        assert attempts == [token]


@pytest.mark.parametrize("mode", ["auto", "off"])
@pytest.mark.parametrize("repeat_key", ["decision", "renamed-decision"])
def test_answered_local_questions_are_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, repeat_key: str
) -> None:
    root = run_root(tmp_path)
    monkeypatch.setenv("FACTORY_HERALD", mode)
    assert_retained(root, post(root, question=True))
    escalate.record_answer(root, key="decision", answer="Tuesday", source="Test owner")
    again = FakeHerald()
    result = post(
        root, key=repeat_key, summary="choose a deployment window!", question=True,
        client=client(again),
    )
    assert result["answered"] is True and result["answer"] == "Tuesday"
    assert result["source"] == "Test owner" and result.get("delivered") is not True
    assert again.messages == []


@pytest.mark.parametrize("failure", ["down", "timeout", "http", "malformed", "rpc", "refusal"])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_transport_failure_after_initialization_is_retained(
    tmp_path: Path, failure: str, operation: str
) -> None:
    root, endpoint = run_root(tmp_path), FakeHerald()
    if operation == "resolve":
        post(root, client=client(FakeHerald()))
    attempts = []

    def transport(url, headers, body):
        message = json.loads(body)
        if message["method"] != "tools/call":
            return endpoint(url, headers, body)
        attempts.append(message)
        if failure == "down":
            raise ConnectionResetError("Synthetic connection loss after initialize")
        if failure == "timeout":
            raise TimeoutError("Synthetic reporting timeout")
        if failure == "http":
            return 503, {"Content-Type": "text/plain"}, b"Temporarily unavailable"
        if failure == "malformed":
            return 200, {"Content-Type": "application/json"}, b"not JSON"
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if failure == "rpc":
            reply["error"] = {"code": -32603, "message": "Synthetic RPC refusal"}
        else:
            reply["result"] = {
                "isError": True, "structuredContent": {"code": "member-not-granted"}
            }
        return 200, {"Content-Type": "application/json"}, json.dumps(reply).encode()

    supplied = escalate.Herald("https://herald.invalid", "synthetic-token", transport=transport)
    result = invoke(root, operation, client=supplied)
    record = assert_retained(root, result)
    assert result["delivery"] == "failed" and result["error"]
    assert record["error"] == result["error"] and len(attempts) == 1


@pytest.mark.parametrize("mode", ["off", "auto"])
@pytest.mark.parametrize(
    "invalid", ["key", "summary", "informational-question", "level", "interval", "unknown-resolve"]
)
def test_optional_reporting_still_rejects_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, invalid: str
) -> None:
    root = run_root(tmp_path)
    monkeypatch.setenv("FACTORY_HERALD", mode)
    with pytest.raises((escalate.EscalationError, ValueError)):
        if invalid == "key":
            post(root, key="space is invalid")
        elif invalid == "summary":
            post(root, summary="")
        elif invalid == "informational-question":
            post(root, level="informational", question=True)
        elif invalid == "level":
            post(root, level="invalid")
        elif invalid == "interval":
            escalate.heartbeat(root, interval=-1)
        else:
            escalate.resolve(root, key="unknown")


@pytest.mark.parametrize("operation", ["post", "heartbeat", "resolve"])
@pytest.mark.parametrize("invalid", ["mode", "not-a-url", "https://", "file:///tmp/x", "ftp://x"])
def test_invalid_configuration_cannot_be_downgraded_to_reporting_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, invalid: str
) -> None:
    root = run_root(tmp_path)
    if operation == "resolve":
        post(root)  # A local-only item must not bypass configuration validation.
    if invalid == "mode":
        monkeypatch.setenv("FACTORY_HERALD", "invalid-value")
    else:
        monkeypatch.setenv("HERALD_FACTORY_TOKEN", "synthetic-token")
        monkeypatch.setenv("HERALD_URL", invalid)
    with pytest.raises((escalate.EscalationError, ValueError)):
        invoke(root, operation)


@pytest.mark.parametrize("operation", ["post", "heartbeat", "resolve", "answer"])
def test_local_evidence_failure_remains_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    root = run_root(tmp_path)
    monkeypatch.setenv("FACTORY_HERALD", "off")
    if operation in ("resolve", "answer"):
        post(root, question=True)
    original_open = builtins.open
    original_path_open = Path.open
    original_os_open = os.open

    def is_evidence(path):
        return not isinstance(path, int) and Path(path).resolve().is_relative_to(root.resolve())

    def unwritable(path, mode="r", *args, **kwargs):
        if is_evidence(path) and any(flag in mode for flag in "wax+"):
            raise PermissionError("Synthetic local evidence write failure")
        return original_open(path, mode, *args, **kwargs)

    def unwritable_path(path, mode="r", *args, **kwargs):
        if is_evidence(path) and any(flag in mode for flag in "wax+"):
            raise PermissionError("Synthetic local evidence write failure")
        return original_path_open(path, mode, *args, **kwargs)

    def unwritable_fd(path, flags, *args, **kwargs):
        if is_evidence(path) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            raise PermissionError("Synthetic local evidence write failure")
        return original_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", unwritable)
    monkeypatch.setattr(Path, "open", unwritable_path)
    monkeypatch.setattr(os, "open", unwritable_fd)
    with pytest.raises((OSError, escalate.EscalationError)):
        if operation == "answer":
            escalate.record_answer(root, key="decision", answer="Tuesday", source="Test owner")
        else:
            invoke(root, operation)


@pytest.mark.parametrize("remote_success", [False, True])
def test_unwritable_local_journal_cannot_claim_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote_success: bool
) -> None:
    root = run_root(tmp_path)
    target = "orchestrator/escalations.jsonl" if remote_success else "events.jsonl"
    (root / target).mkdir(parents=True)
    if not remote_success:
        monkeypatch.setenv("FACTORY_HERALD", "off")
    with pytest.raises((OSError, escalate.EscalationError)):
        post(root, client=client(FakeHerald()))


CLI_BOOTSTRAP = """\
import runpy, socket, sys, urllib.request

def unavailable(*args, **kwargs):
    raise ConnectionRefusedError("Synthetic unavailable reporting endpoint")

socket.socket.connect = unavailable
socket.create_connection = unavailable
urllib.request.urlopen = unavailable
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def cli(root: Path, mode: str, command: str, *arguments: str, token: bool = False):
    env = {
        "HOME": os.environ["HOME"],
        "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
        "PATH": os.defpath,
        "PYTHONPATH": str(CLI.parent),
        "FACTORY_HERALD": mode,
    }
    if token:
        env["HERALD_FACTORY_TOKEN"] = "synthetic-cli-token"
    return subprocess.run(
        [sys.executable, "-c", CLI_BOOTSTRAP, str(CLI), command, "--root", str(root), *arguments],
        env=env, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.parametrize(("mode", "token"), [("off", False), ("auto", False), ("auto", True)])
@pytest.mark.parametrize("operation", OPERATIONS)
def test_cli_reporting_degradation_exits_successfully(
    tmp_path: Path, mode: str, token: bool, operation: str
) -> None:
    root = run_root(tmp_path)
    arguments = ["--level", "important", "--key", "decision", "--summary", "Retained CLI evidence"]
    if operation == "resolve":
        first = cli(root, mode, "post", *arguments, token=token)
        assert first.returncode == 0, first.stdout + first.stderr
        result = cli(root, mode, "resolve", "--key", "decision", token=token)
    elif operation == "heartbeat":
        result = cli(root, mode, "heartbeat", "--interval", "600", "--done", token=token)
    else:
        if operation == "question":
            arguments.append("--question")
        result = cli(root, mode, "post", *arguments, token=token)
    assert result.returncode == 0, result.stdout + result.stderr
    assert_retained(root, json.loads(result.stdout))


@pytest.mark.parametrize("invalid", ["mode", "key", "interval", "root-write", "resolve-mode"])
def test_cli_configuration_input_and_evidence_errors_have_nonzero_exit(
    tmp_path: Path, invalid: str
) -> None:
    root = run_root(tmp_path)
    mode = "invalid-value" if invalid == "mode" else "off"
    if invalid == "root-write":
        (root / "orchestrator").write_text("Not an evidence directory")
    if invalid == "interval":
        result = cli(root, mode, "heartbeat", "--interval", "-1")
    elif invalid == "resolve-mode":
        post(root)
        result = cli(root, "invalid-value", "resolve", "--key", "decision")
    else:
        result = cli(
            root, mode, "post", "--level", "important", "--key",
            "has spaces" if invalid == "key" else "decision", "--summary", "Test",
        )
    assert result.returncode != 0, result.stdout + result.stderr
    assert '"delivered": true' not in result.stdout


@pytest.mark.parametrize("operation", ["post", "heartbeat", "resolve"])
def test_configured_json_responses_keep_remote_success_contract(
    tmp_path: Path, operation: str
) -> None:
    root, endpoint = run_root(tmp_path), FakeHerald()
    if operation == "resolve":
        post(root, client=client(FakeHerald()))

    def json_transport(url, headers, body):
        status, response_headers, response_body = endpoint(url, headers, body)
        if response_headers.get("Content-Type") == "text/event-stream":
            payload = next(
                line.removeprefix(b"data: ")
                for line in response_body.splitlines() if line.startswith(b"data: ")
            )
            return status, {"Content-Type": "application/json"}, payload
        return status, response_headers, response_body

    supplied = escalate.Herald(
        "https://herald.invalid", "synthetic-token", transport=json_transport
    )
    result = invoke(root, operation, client=supplied)
    assert result["delivered"] is True
    record = json.loads((root / "orchestrator/escalations.jsonl").read_text().splitlines()[-1])
    assert record["delivered"] is True
    [(name, arguments)] = endpoint.calls()
    assert arguments["contract_version"] == 1
    if operation == "post":
        assert name == "needs_attention" and arguments["class"] == "at-your-convenience"
    elif operation == "heartbeat":
        assert name == "heartbeat" and arguments["interval_seconds"] == 432
        assert arguments["done"] is True
    else:
        assert name == "resolve" and arguments["occurrence"] == 7
        assert arguments["key"] == "factory/demo-run/decision"


@pytest.mark.parametrize("mode", ["auto", "off"])
def test_cli_local_answers_suppress_repeated_questions(tmp_path: Path, mode: str) -> None:
    root = run_root(tmp_path)
    first = cli(
        root, mode, "post", "--level", "critical", "--key", "decision",
        "--summary", "Which window?", "--question",
    )
    assert first.returncode == 0, first.stdout + first.stderr
    answer = cli(
        root, mode, "answer", "--key", "decision", "--answer", "Tuesday", "--source", "Test owner"
    )
    assert answer.returncode == 0, answer.stdout + answer.stderr
    again = cli(
        root, mode, "post", "--level", "critical", "--key", "new-decision",
        "--summary", "which window", "--question",
    )
    assert again.returncode == 3, again.stdout + again.stderr
    result = json.loads(again.stdout)
    assert result["answered"] is True and result["answer"] == "Tuesday"
    assert result["source"] == "Test owner" and result.get("delivered") is not True
