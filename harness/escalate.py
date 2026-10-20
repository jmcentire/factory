#!/usr/bin/env python3
"""The Orchestrator's outward channel: run facts to the founder's Herald dash (its /mcp).

Three levels, chosen by who must act:
- informational: a status update. `post_update` with `status`; the founder need not act.
- important: a decision the run made that the founder should see. `needs_attention`, class
  `at-your-convenience`.
- critical: something only the founder can do, or a genuine impasse (no role in the run knows
  what needs doing). `needs_attention`, class `critical`.

A question is asked once. Before a question goes out, the question ledger is checked; an
answered question is refused here and its answer printed, so the seat that asked uses it. An
unreachable Herald is never silent: the attempt is journalled and the exit status says so.

The token is a Herald `factory-run` agent token (`herald token issue <agent> --kind factory-run`),
read from HERALD_FACTORY_TOKEN or ~/.harness/secrets/HERALD_FACTORY_TOKEN. It is never printed.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

PROTOCOL = "2025-11-25"
USER_AGENT = "factory-orchestrator/0.8.21 (+https://github.com/jmcentire/factory)"
CONTRACT_VERSION = 1
LEVELS = ("informational", "important", "critical")
CLASS_FOR = {"important": "at-your-convenience", "critical": "critical"}
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
TIMEOUT = 15
EXIT_ANSWERED = 3
EXIT_UNREACHABLE = 75
EXIT_USAGE = 64

Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, Mapping[str, str], bytes]]


class EscalationError(RuntimeError):
    """Herald refused or could not be reached; the caller sees it, nothing is assumed."""


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _orchestrator_dir(root: pathlib.Path) -> pathlib.Path:
    path = root / "orchestrator"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _append(path: pathlib.Path, row: Mapping[str, object]) -> None:
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _rows(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def normalize(question: str) -> str:
    "One spelling of a question for the exact-repeat check; the Orchestrator judges paraphrases."
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", question.lower()).split())


def question_digest(question: str) -> str:
    return "sha256:" + hashlib.sha256(normalize(question).encode("utf-8")).hexdigest()


def prior_question(root: pathlib.Path, key: str, question: str) -> dict[str, Any] | None:
    "The newest asking of this key or this exact question, with the founder's answer folded in."
    digest = question_digest(question)
    rows = _rows(_orchestrator_dir(root) / "questions.jsonl")
    asked = [
        r
        for r in rows
        if r.get("kind") != "answer" and (r.get("key") == key or r.get("question_digest") == digest)
    ]
    if not asked:
        return None
    keys = {r.get("key") for r in asked}
    found = dict(asked[-1])
    for row in rows:
        if row.get("kind") == "answer" and row.get("key") in keys:
            found.update(answer=row.get("answer"), answer_source=row.get("source"))
    return found


def token() -> str:
    value = os.environ.get("HERALD_FACTORY_TOKEN", "").strip()
    if not value:
        path = pathlib.Path.home() / ".harness" / "secrets" / "HERALD_FACTORY_TOKEN"
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
    if not value:
        raise EscalationError(
            "no Herald factory-run token: run `herald token issue factory --kind factory-run` and "
            "store it in ~/.harness/secrets/HERALD_FACTORY_TOKEN (0600)"
        )
    return value


def _urllib(
    url: str, headers: Mapping[str, str], body: bytes
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read(256 * 1024)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read(64 * 1024)


def _decode(content_type: str, body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = data[-1] if data else ""
    try:
        value = json.loads(text)
    except ValueError as error:
        raise EscalationError(
            f"Herald answered with unparseable JSON-RPC: {text[:200]!r}"
        ) from error
    if not isinstance(value, dict):
        raise EscalationError("Herald answered with a non-object JSON-RPC message")
    return value


def _title(body: bytes) -> str:
    "A refusal's own title (problem+json), never its whole body."
    try:
        value = json.loads(body)
    except ValueError:
        return ""
    title = value.get("title") if isinstance(value, dict) else None
    return str(title)[:120] if title else ""


class Herald:
    "A 2025-form MCP client for one Herald agent: initialize once, then tools/call."

    def __init__(self, url: str, bearer: str, transport: Transport = _urllib) -> None:
        self.url = url.rstrip("/") + "/mcp"
        self.bearer = bearer
        self.transport = transport
        self.session = ""
        self.next_id = 1

    def _rpc(
        self, method: str, params: Mapping[str, object] | None, *, notify: bool = False
    ) -> Any:
        message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        if not notify:
            message["id"] = self.next_id
            self.next_id += 1
        headers = {
            "Authorization": f"Bearer {self.bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
            # Cloudflare's browser integrity check refuses Python's default agent (error 1010).
            "User-Agent": USER_AGENT,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        try:
            status, reply_headers, body = self.transport(
                self.url, headers, json.dumps(message).encode()
            )
        except OSError as error:
            raise EscalationError(f"Herald unreachable: {error.__class__.__name__}") from error
        lowered = {k.lower(): v for k, v in reply_headers.items()}
        self.session = lowered.get("mcp-session-id", self.session)
        content_type = lowered.get("content-type", "")
        jsonrpc = "event-stream" in content_type or b'"jsonrpc"' in body[:200]
        if status >= 400 and not jsonrpc:
            raise EscalationError(f"Herald refused {method}: HTTP {status} {_title(body)}".strip())
        if notify:
            return None
        reply = _decode(content_type, body)
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise EscalationError(f"Herald refused {method}: {error.get('message', 'error')}")
        return reply.get("result")

    def open(self) -> None:
        self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "factory-orchestrator", "version": "1"},
            },
        )
        self._rpc("notifications/initialized", None, notify=True)

    def call(self, tool: str, arguments: Mapping[str, object]) -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": tool, "arguments": dict(arguments)})
        if not isinstance(result, dict):
            raise EscalationError(f"Herald answered {tool} with no result")
        structured = result.get("structuredContent")
        if result.get("isError"):
            code = structured.get("code") if isinstance(structured, dict) else None
            raise EscalationError(f"Herald refused {tool}: {code or 'error'}")
        return structured if isinstance(structured, dict) else {}


def _run_name(root: pathlib.Path) -> str:
    return root.resolve().name


def _scoped_key(root: pathlib.Path, key: str) -> str:
    scoped = f"factory/{_run_name(root)}/{key}"
    if not KEY.match(scoped):
        raise EscalationError(f"key must match {KEY.pattern} once scoped: {scoped!r}")
    return scoped


def escalate(
    root: pathlib.Path,
    *,
    level: str,
    key: str,
    summary: str,
    detail: str = "",
    question: bool = False,
    client: Herald | None = None,
) -> dict[str, Any]:
    if level not in LEVELS:
        raise EscalationError(f"level must be one of {', '.join(LEVELS)}")
    summary = " ".join(summary.split())
    if not summary:
        raise EscalationError("summary is required")
    scoped = _scoped_key(root, key)
    folder = _orchestrator_dir(root)
    if question:
        if level == "informational":
            raise EscalationError(
                "a question for the founder is important or critical, never informational"
            )
        prior = prior_question(root, scoped, summary + "\n" + detail)
        if prior and prior.get("answer"):
            return {
                "answered": True,
                "key": scoped,
                "answer": prior["answer"],
                "source": prior.get("answer_source"),
            }
    herald = client or Herald(os.environ.get("HERALD_URL", "https://dash.herald.tools"), token())
    row: dict[str, Any] = {"at": _now(), "level": level, "key": scoped, "summary": summary[:200]}
    try:
        herald.open()
        if level == "informational":
            status = f"{_run_name(root)}: {summary}"
            if detail:
                status += f" -- {' '.join(detail.split())}"
            result = herald.call(
                "post_update", {"contract_version": CONTRACT_VERSION, "status": status[:500]}
            )
        else:
            arguments: dict[str, object] = {
                "contract_version": CONTRACT_VERSION,
                "key": scoped,
                "class": CLASS_FOR[level],
                "summary": summary[:200],
            }
            if detail:
                arguments["detail"] = detail[:1000]
            result = herald.call("needs_attention", arguments)
            row["occurrence"] = result.get("occurrence")
    except EscalationError as error:
        row.update({"delivered": False, "error": str(error)})
        _append(folder / "escalations.jsonl", row)
        _append(
            root / "events.jsonl",
            {
                "ts": row["at"],
                "kind": "escalation-failed",
                "class": "attention",
                "source": "escalate.py",
                "wake": True,
                "detail": f"{level} {scoped}: {error}",
            },
        )
        raise
    row["delivered"] = True
    _append(folder / "escalations.jsonl", row)
    if question:
        _append(
            folder / "questions.jsonl",
            {
                "kind": "question",
                "at": row["at"],
                "key": scoped,
                "level": level,
                "question": summary[:200],
                "question_digest": question_digest(summary + "\n" + detail),
            },
        )
    return {
        "answered": False,
        "key": scoped,
        "delivered": True,
        "occurrence": row.get("occurrence"),
    }


def record_answer(root: pathlib.Path, *, key: str, answer: str, source: str) -> dict[str, Any]:
    "The founder's answer, recorded once; the same question is never asked again."
    scoped = _scoped_key(root, key)
    answer = answer.strip()
    if not answer or not source.strip():
        raise EscalationError("an answer needs its text and its source (where the founder said it)")
    row = {
        "kind": "answer",
        "at": _now(),
        "key": scoped,
        "answer": answer[:4000],
        "source": source[:400],
    }
    _append(_orchestrator_dir(root) / "questions.jsonl", row)
    return row


def resolve(root: pathlib.Path, *, key: str, client: Herald | None = None) -> dict[str, Any]:
    scoped = _scoped_key(root, key)
    occurrence = None
    for row in _rows(_orchestrator_dir(root) / "escalations.jsonl"):
        if row.get("key") == scoped and row.get("occurrence"):
            occurrence = row["occurrence"]
    if occurrence is None:
        raise EscalationError(f"no delivered attention item for {scoped}")
    herald = client or Herald(os.environ.get("HERALD_URL", "https://dash.herald.tools"), token())
    herald.open()
    herald.call(
        "resolve", {"contract_version": CONTRACT_VERSION, "key": scoped, "occurrence": occurrence}
    )
    _append(
        _orchestrator_dir(root) / "escalations.jsonl",
        {
            "at": _now(),
            "level": "resolved",
            "key": scoped,
            "occurrence": occurrence,
            "delivered": True,
        },
    )
    return {"key": scoped, "resolved": occurrence}


def heartbeat(
    root: pathlib.Path, *, interval: int, done: bool = False, client: Herald | None = None
) -> None:
    "The factory's dead-man switch: Herald raises a stale alert when these stop, unless done."
    herald = client or Herald(os.environ.get("HERALD_URL", "https://dash.herald.tools"), token())
    herald.open()
    arguments: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "interval_seconds": interval,
    }
    if done:
        arguments["done"] = True
    herald.call("heartbeat", arguments)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    post = sub.add_parser("post", help="send one status, decision or critical item")
    post.add_argument("--root", required=True, type=pathlib.Path)
    post.add_argument("--level", required=True, choices=LEVELS)
    post.add_argument("--key", required=True)
    post.add_argument("--summary", required=True)
    post.add_argument("--detail", default="")
    post.add_argument(
        "--question",
        action="store_true",
        help="a question at a genuine impasse, where no role in the run can act",
    )
    answer = sub.add_parser("answer", help="record the founder's answer to a question")
    answer.add_argument("--root", required=True, type=pathlib.Path)
    answer.add_argument("--key", required=True)
    answer.add_argument("--answer", required=True)
    answer.add_argument("--source", required=True)
    done = sub.add_parser("resolve", help="resolve an attention item the run no longer needs")
    done.add_argument("--root", required=True, type=pathlib.Path)
    done.add_argument("--key", required=True)
    beat = sub.add_parser("heartbeat", help="the factory's dead-man heartbeat")
    beat.add_argument("--root", required=True, type=pathlib.Path)
    beat.add_argument("--interval", type=int, default=300)
    beat.add_argument("--done", action="store_true", help="the run ended; silence is expected")
    args = parser.parse_args(argv)
    try:
        if args.command == "post":
            result = escalate(
                args.root,
                level=args.level,
                key=args.key,
                summary=args.summary,
                detail=args.detail,
                question=args.question,
            )
            print(json.dumps(result, sort_keys=True))
            return EXIT_ANSWERED if result.get("answered") else 0
        if args.command == "answer":
            print(
                json.dumps(
                    record_answer(args.root, key=args.key, answer=args.answer, source=args.source)
                )
            )
            return 0
        if args.command == "resolve":
            print(json.dumps(resolve(args.root, key=args.key)))
            return 0
        heartbeat(args.root, interval=args.interval, done=args.done)
        return 0
    except EscalationError as error:
        print(f"escalate: {error}", file=sys.stderr)
        return (
            EXIT_UNREACHABLE
            if "unreachable" in str(error) or "refused" in str(error)
            else EXIT_USAGE
        )


if __name__ == "__main__":
    raise SystemExit(main())
