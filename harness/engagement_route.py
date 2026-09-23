#!/usr/bin/env python3
"""Route one item the run could have stopped for, and retain what happened.

This is the executable half of `factory_core/engagement.py`: the core decides,
this records. Below interactive engagement the registers are the human's whole
view of what was decided for them, so nothing routes without landing in one.

    engagement_route.py item --root <run> --subject <text> --basis weak \\
        --reversibility irreversible --critical --undeterminable --owned-here

    engagement_route.py announce --root <run> --action "push tag v1.2.3" \\
        --reversibility irreversible

`item` is a question the run would otherwise have asked. `announce` is a
consequential action about to be taken. Both print the routing as JSON and
exit 0; only interactive engagement ever answers "block", and the caller is
what honors it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys

_HARNESS_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)

from factory_core.engagement import (  # noqa: E402 - repository package, resolved above
    BLOCK_FOR_HUMAN,
    INTERACTIVE,
    IRREVERSIBLE,
    NOTIFY_CHANNEL,
    RECORD_ASSUMPTION,
    RECORD_ESCALATION,
    EngagementError,
    Item,
    Routing,
    announces_before_acting,
    normalize,
    route,
)
from lane_dialogue import run_engagement  # noqa: E402 - adjacent harness module

REGISTERS = {
    RECORD_ASSUMPTION: "assumptions.jsonl",
    RECORD_ESCALATION: "escalations.jsonl",
    NOTIFY_CHANNEL: "escalations.jsonl",
}
SIDE_EFFECTS = "side-effects.jsonl"
_MAX_SUBJECT = 4096


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _append(root: pathlib.Path, filename: str, row: dict[str, object]) -> pathlib.Path:
    directory = root / "registers"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
    return path


def _channel(root: pathlib.Path) -> str | None:
    try:
        metadata = json.loads((root / "harness.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = metadata.get("question_channel") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _send(root: pathlib.Path, subject: str) -> dict[str, object]:
    """Hand the question to the configured channel. Never blocks the run.

    A channel that fails is recorded as unsent rather than retried forever: the
    escalation is already in the register, so the human sees it either way.
    """

    command = _channel(root)
    if not command:
        return {"sent": False, "reason": "no question_channel configured; recorded only"}
    try:
        finished = subprocess.run(
            [*shlex.split(command)],
            input=subject,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"sent": False, "reason": f"channel failed: {exc}"}
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip()[:200]
        return {"sent": False, "reason": f"channel exited {finished.returncode}: {detail}"}
    return {"sent": True, "reason": "delivered to the configured channel"}


def _item(arguments: argparse.Namespace) -> int:
    root = pathlib.Path(arguments.root).resolve()
    engagement = run_engagement(root)
    subject = arguments.subject.strip()
    if not subject or len(subject.encode("utf-8")) > _MAX_SUBJECT:
        print("engagement-route refused: a bounded subject is required", file=sys.stderr)
        return 64
    item = Item(
        determinable=not arguments.undeterminable,
        owned_here=arguments.owned_here,
        basis=arguments.basis,
        reversibility=arguments.reversibility,
        critical=arguments.critical,
    )
    routing: Routing = route(engagement, item)
    record: dict[str, object] = {
        "ts": _now(),
        "engagement": engagement,
        "subject": subject,
        "action": routing.action,
        "reason": routing.reason,
        "basis": item.basis,
        "reversibility": item.reversibility,
        "critical": item.critical,
    }
    if routing.action == NOTIFY_CHANNEL:
        record["delivery"] = _send(root, subject)
    filename = REGISTERS.get(routing.action)
    if filename:
        _append(root, filename, record)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


def _announce(arguments: argparse.Namespace) -> int:
    """A consequential action: announced at interactive, recorded below it."""

    root = pathlib.Path(arguments.root).resolve()
    engagement = run_engagement(root)
    holds = announces_before_acting(engagement) and arguments.reversibility == IRREVERSIBLE
    record = {
        "ts": _now(),
        "engagement": engagement,
        "action": arguments.action.strip(),
        "reversibility": arguments.reversibility,
        "inverse": arguments.inverse or None,
        "disposition": BLOCK_FOR_HUMAN if holds else "proceeded",
    }
    _append(root, SIDE_EFFECTS, record)
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    item = sub.add_parser("item", help="route a question the run would otherwise ask")
    item.add_argument("--root", required=True)
    item.add_argument("--subject", required=True)
    item.add_argument("--undeterminable", action="store_true")
    item.add_argument("--owned-here", action="store_true")
    item.add_argument("--basis", default="strong")
    item.add_argument("--reversibility", default="reversible")
    item.add_argument("--critical", action="store_true")

    announce = sub.add_parser("announce", help="record a consequential action")
    announce.add_argument("--root", required=True)
    announce.add_argument("--action", required=True)
    announce.add_argument("--reversibility", default="reversible")
    announce.add_argument("--inverse", default="")

    arguments = parser.parse_args()
    try:
        if arguments.command == "item":
            return _item(arguments)
        return _announce(arguments)
    except EngagementError as exc:
        print(f"engagement-route refused: {exc}", file=sys.stderr)
        return 64
    except OSError as exc:
        print(f"engagement-route refused: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    # An unconfigured run is treated as interactive by run_engagement(), never
    # as autonomous: silence is not consent to run dark.
    assert normalize(INTERACTIVE) == INTERACTIVE
    raise SystemExit(main())
