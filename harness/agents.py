#!/usr/bin/env python3
"""agents — one registry of the agent harnesses a Factory seat can run, so any harness can hold
any seat.

The factory names no model and no vendor. What it needs from a harness is a shape: how to start a
headless turn in a working directory with a named model and reasoning effort, how to hand it the
prompt, how to resume the same session for the next turn, which credential variables it reads,
and how to read the final message, session id and cost back out of its output. Each harness is
data here; launchers (``tmux_lane.sh``, ``factory.sh``) ask the registry instead of carrying
per-vendor branches, and a new harness is one entry.

Invariant: an entry builds argv only — no shell, no string interpolation — and never puts a
credential value on a command line; credentials pass by variable *name* (``credential_env``) so
a launcher that clears the environment can admit exactly those.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Turn:
    """One headless turn: the argv, how the prompt travels, and where a final message lands."""

    argv: tuple[str, ...]
    prompt_via: str  # "stdin" | "argv" (appended as the last argument)
    last_message_file: str = ""


@dataclass(frozen=True)
class Outcome:
    text: str
    session: str
    cost_usd: float
    tokens: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Harness:
    name: str
    executable: str
    credential_env: tuple[str, ...]  # variable names a cleared environment must admit
    resumable: bool

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400) -> Turn:
        raise NotImplementedError

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        raise NotImplementedError

    def noise(self, line: str) -> bool:
        """A streaming-progress line the transcript need not keep (outcome never reads it)."""

        return False


def _last_json(text: str) -> dict[str, object]:
    for line in reversed(text.strip().splitlines()):
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            return doc
    return {}


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


class ClaudeCode(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400) -> Turn:
        argv = ["claude", "-p", "--output-format", "json", "--permission-mode",
                "bypassPermissions", "--max-turns", str(max_turns)]
        argv += ["--model", model] if model else []
        argv += ["--effort", effort] if effort else []
        argv += ["--resume", resume] if resume else []
        return Turn(tuple(argv), "stdin")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        doc = _last_json(stdout)
        usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return Outcome(
            text=str(doc.get("result") or ""), session=str(doc.get("session_id") or ""),
            cost_usd=float(str(doc.get("total_cost_usd") or 0.0)),
            tokens={"input": _int(usage.get("input_tokens"))
                    + _int(usage.get("cache_creation_input_tokens")),
                    "cached": _int(usage.get("cache_read_input_tokens")),
                    "output": _int(usage.get("output_tokens"))},
        )


class Codex(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400) -> Turn:
        common = ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
        common += ["-o", last_message_file] if last_message_file else []
        common += ["-m", model] if model else []
        common += ["-c", f"model_reasoning_effort={json.dumps(effort)}"] if effort else []
        if resume:
            argv = ["codex", "exec", "resume", *common, resume, "-"]
        else:
            argv = ["codex", "exec", *common, "-C", str(cwd), "-"]
        return Turn(tuple(argv), "stdin", last_message_file)

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        session, tokens = "", {"input": 0, "cached": 0, "output": 0}
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started" and event.get("thread_id"):
                session = str(event["thread_id"])
            usage = event.get("usage")
            if event.get("type") == "turn.completed" and isinstance(usage, dict):
                tokens["input"] += _int(usage.get("input_tokens"))
                tokens["cached"] += _int(usage.get("cached_input_tokens"))
                tokens["output"] += _int(usage.get("output_tokens"))
        return Outcome(text=last_message, session=session, cost_usd=0.0, tokens=tokens)


class Antigravity(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400) -> Turn:
        argv = ["agy", "--output-format", "json", "--dangerously-skip-permissions",
                "--add-dir", str(cwd)]
        argv += ["--model", model] if model else []
        argv += ["--effort", effort] if effort else []
        argv += ["--conversation", resume] if resume else []
        argv += ["--print"]  # the prompt follows as the last argument
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        doc = _last_json(stdout)
        usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return Outcome(
            text=str(doc.get("response") or ""), session=str(doc.get("conversation_id") or ""),
            cost_usd=0.0,
            tokens={"input": _int(usage.get("input_tokens")),
                    "cached": _int(usage.get("cache_read_tokens")),
                    "output": _int(usage.get("output_tokens"))},
        )


class PrimeAgent(Harness):
    # JSON mode re-emits the whole growing message on every streamed token; kept, one long
    # session's log grows quadratically into gigabytes. outcome() reads only message_end.
    def noise(self, line: str) -> bool:
        return re.search(r'"type"\s*:\s*"message_update"', line[:160]) is not None

    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400) -> Turn:
        argv = ["prime-agent", "--print", "--mode", "json", "--cwd", str(cwd)]
        provider, _, name = model.partition("/") if model.count("/") >= 2 else ("", "", model)
        argv += ["--provider", provider] if provider else []
        argv += ["--model", name] if name else []
        argv += ["--thinking", effort] if effort else []
        argv += ["--resume", resume] if resume else []
        argv += ["--"]
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        session, text, cost = "", "", 0.0
        tokens = {"input": 0, "cached": 0, "output": 0}
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "session" and event.get("id"):
                session = str(event["id"])
            message = event.get("message")
            if event.get("type") == "message_end" and isinstance(message, dict) \
                    and message.get("role") == "assistant":
                usage = message.get("usage") or {}
                if isinstance(usage, dict):
                    total = (usage.get("cost") or {}).get("total") \
                        if isinstance(usage.get("cost"), dict) else 0
                    cost += float(total or 0)
                    tokens["input"] += _int(usage.get("input"))
                    tokens["cached"] += _int(usage.get("cacheRead"))
                    tokens["output"] += _int(usage.get("output"))
                parts = message.get("content") or []
                said = "".join(p.get("text", "") for p in parts
                               if isinstance(p, dict) and p.get("type") == "text")
                text = said or text
        return Outcome(text=text, session=session, cost_usd=cost, tokens=tokens)


class CursorAgent(Harness):
    def turn(self, *, model: str, effort: str, cwd: Path, resume: str = "",
             last_message_file: str = "", max_turns: int = 400) -> Turn:
        argv = ["agent", "-p", "--force", "--trust", "--workspace", str(cwd)]
        argv += ["--model", model] if model else []
        return Turn(tuple(argv), "argv")

    def outcome(self, stdout: str, last_message: str = "") -> Outcome:
        return Outcome(text=stdout.strip()[-20_000:], session="", cost_usd=0.0)


HARNESSES: Mapping[str, Harness] = {
    "claude": ClaudeCode("claude", "claude", ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
                                              "CLAUDE_CONFIG_DIR", "IS_SANDBOX"), True),
    "codex": Codex("codex", "codex", ("OPENAI_API_KEY", "CODEX_HOME"), True),
    "agy": Antigravity("agy", "agy", (), True),
    "prime-agent": PrimeAgent("prime-agent", "prime-agent", ("OPENROUTER_API_KEY",), True),
    "cursor-agent": CursorAgent("cursor-agent", "agent", ("CURSOR_API_KEY",), False),
}


def harness(name: str) -> Harness:
    try:
        return HARNESSES[name]
    except KeyError:
        raise SystemExit(f"agents: unknown harness {name!r} ({', '.join(HARNESSES)})") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="the registered harnesses")
    turn = sub.add_parser("turn", help="print one headless turn's argv, NUL-separated")
    turn.add_argument("--agent", required=True)
    turn.add_argument("--model", default="")
    turn.add_argument("--effort", default="")
    turn.add_argument("--cwd", required=True, type=Path)
    turn.add_argument("--resume", default="")
    env = sub.add_parser("credential-env", help="print the credential variable names, one a line")
    env.add_argument("--agent", required=True)
    args = parser.parse_args(argv)
    if args.command == "list":
        print("\n".join(HARNESSES))
    elif args.command == "credential-env":
        print("\n".join(harness(args.agent).credential_env))
    else:
        built = harness(args.agent).turn(model=args.model, effort=args.effort, cwd=args.cwd,
                                         resume=args.resume)
        sys.stdout.write("\0".join((built.prompt_via, *built.argv)) + "\0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
