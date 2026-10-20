"""The Orchestrator's outward channel: three levels to Herald, each question asked once."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

import harness.orchestrator_channel  # noqa: F401 - establish adjacent harness imports

escalate = importlib.import_module("escalate")


class FakeHerald:
    "Records every JSON-RPC message and answers like Herald's 2025-form /mcp."

    def __init__(self, *, refuse: str = "", down: bool = False) -> None:
        self.messages: list[dict] = []
        self.headers: list[dict] = []
        self.refuse = refuse
        self.down = down

    def __call__(self, url, headers, body):
        if self.down:
            raise ConnectionRefusedError("down")
        message = json.loads(body)
        self.messages.append(message)
        self.headers.append(dict(headers))
        if "id" not in message:
            return 202, {}, b""
        if message["method"] == "initialize":
            result = {"protocolVersion": escalate.PROTOCOL, "capabilities": {"tools": {}}}
        else:
            name = message["params"]["name"]
            if name == self.refuse:
                result = {"isError": True, "structuredContent": {"code": "member-not-granted"}}
            elif name == "needs_attention":
                result = {
                    "structuredContent": {
                        "occurrence": 7,
                        "state": "open",
                        "count": 1,
                        "class": message["params"]["arguments"]["class"],
                    }
                }
            else:
                result = {"structuredContent": {"ok": True}}
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        return (
            200,
            {"Content-Type": "text/event-stream"},
            f"event: message\ndata: {json.dumps(reply)}\n\n".encode(),
        )

    def calls(self) -> list[tuple[str, dict]]:
        return [
            (m["params"]["name"], m["params"]["arguments"])
            for m in self.messages
            if m.get("method") == "tools/call"
        ]


def run_root(tmp_path: Path) -> Path:
    root = tmp_path / "demo-run"
    root.mkdir()
    return root


def client(fake: FakeHerald) -> escalate.Herald:
    return escalate.Herald("https://dash.example", "tok", transport=fake)


@pytest.mark.parametrize(
    ("level", "tool", "klass"),
    [
        ("informational", "post_update", None),
        ("important", "needs_attention", "at-your-convenience"),
        ("critical", "needs_attention", "critical"),
    ],
)
def test_each_level_reaches_its_herald_tool(
    tmp_path: Path, level: str, tool: str, klass: str | None
) -> None:
    root, fake = run_root(tmp_path), FakeHerald()
    result = escalate.escalate(
        root,
        level=level,
        key="deploy",
        summary="Deployed candidate 12",
        detail="af784ea live",
        client=client(fake),
    )
    assert result["delivered"] is True
    assert fake.messages[0]["method"] == "initialize"
    assert all(h["MCP-Protocol-Version"] == escalate.PROTOCOL for h in fake.headers)
    assert all(h["Authorization"] == "Bearer tok" for h in fake.headers)
    assert all(h["User-Agent"].startswith("factory-orchestrator/") for h in fake.headers)
    [(name, arguments)] = fake.calls()
    assert name == tool and arguments["contract_version"] == 1
    if klass:
        assert arguments["class"] == klass and arguments["key"] == "factory/demo-run/deploy"
    else:
        assert arguments["status"].startswith("demo-run: Deployed candidate 12")
    ledger = [
        json.loads(line)
        for line in (root / "orchestrator" / "escalations.jsonl").read_text().splitlines()
    ]
    assert ledger[-1]["level"] == level and ledger[-1]["delivered"] is True


def test_an_answered_question_is_never_asked_again(tmp_path: Path) -> None:
    root, fake = run_root(tmp_path), FakeHerald()
    escalate.escalate(
        root,
        level="critical",
        key="ipad",
        summary="Open /alerts on the iPad for DM-1",
        question=True,
        client=client(fake),
    )
    escalate.record_answer(
        root, key="ipad", answer="Done, it shows alerts.", source="chat 2026-10-20"
    )
    again = FakeHerald()
    result = escalate.escalate(
        root,
        level="critical",
        key="ipad",
        summary="Open /alerts on the iPad for DM-1",
        question=True,
        client=client(again),
    )
    assert result == {
        "answered": True,
        "key": "factory/demo-run/ipad",
        "answer": "Done, it shows alerts.",
        "source": "chat 2026-10-20",
    }
    assert again.messages == [], "an answered question reached Herald again"


def test_the_same_question_under_a_new_key_is_still_a_repeat(tmp_path: Path) -> None:
    root, fake = run_root(tmp_path), FakeHerald()
    escalate.escalate(
        root,
        level="critical",
        key="ipad",
        summary="Open /alerts on the iPad!",
        question=True,
        client=client(fake),
    )
    escalate.record_answer(root, key="ipad", answer="Done.", source="chat")
    result = escalate.escalate(
        root,
        level="critical",
        key="ipad-2",
        summary="open /alerts on the iPad",
        question=True,
        client=client(FakeHerald()),
    )
    assert result["answered"] is True


def test_a_question_is_never_informational(tmp_path: Path) -> None:
    with pytest.raises(escalate.EscalationError):
        escalate.escalate(
            run_root(tmp_path),
            level="informational",
            key="q",
            summary="Shall I?",
            question=True,
            client=client(FakeHerald()),
        )


def test_unreachable_herald_is_journalled_and_raised(tmp_path: Path) -> None:
    root = run_root(tmp_path)
    with pytest.raises(escalate.EscalationError, match="unreachable"):
        escalate.escalate(
            root, level="critical", key="down", summary="x", client=client(FakeHerald(down=True))
        )
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    assert events[-1]["kind"] == "escalation-failed" and events[-1]["wake"] is True


def test_a_refusal_names_herald_code(tmp_path: Path) -> None:
    with pytest.raises(escalate.EscalationError, match="member-not-granted"):
        escalate.escalate(
            run_root(tmp_path),
            level="informational",
            key="s",
            summary="x",
            client=client(FakeHerald(refuse="post_update")),
        )


def test_resolve_uses_the_delivered_occurrence(tmp_path: Path) -> None:
    root, fake = run_root(tmp_path), FakeHerald()
    escalate.escalate(
        root, level="important", key="scope", summary="Email triage deferred", client=client(fake)
    )
    later = FakeHerald()
    escalate.resolve(root, key="scope", client=client(later))
    [(name, arguments)] = later.calls()
    assert name == "resolve" and arguments == {
        "contract_version": 1,
        "key": "factory/demo-run/scope",
        "occurrence": 7,
    }


def test_a_key_must_survive_scoping(tmp_path: Path) -> None:
    with pytest.raises(escalate.EscalationError, match="key must match"):
        escalate.escalate(
            run_root(tmp_path),
            level="important",
            key="has space",
            summary="x",
            client=client(FakeHerald()),
        )


def test_missing_token_says_how_to_mint_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HERALD_FACTORY_TOKEN", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(escalate.EscalationError, match="herald token issue"):
        escalate.token()


def test_an_edge_refusal_names_its_status_and_title(tmp_path: Path) -> None:
    def cloudflare(url, headers, body):
        problem = {"title": "Error 1010: Access denied", "status": 403}
        return 403, {"Content-Type": "application/json"}, json.dumps(problem).encode()

    with pytest.raises(escalate.EscalationError, match="HTTP 403 Error 1010: Access denied"):
        escalate.escalate(
            run_root(tmp_path),
            level="critical",
            key="edge",
            summary="x",
            client=escalate.Herald("https://dash.example", "tok", transport=cloudflare),
        )


def test_a_done_heartbeat_tells_herald_silence_is_expected(tmp_path: Path) -> None:
    fake = FakeHerald()
    escalate.heartbeat(run_root(tmp_path), interval=600, done=True, client=client(fake))
    assert fake.calls() == [
        ("heartbeat", {"contract_version": 1, "interval_seconds": 600, "done": True})
    ]
