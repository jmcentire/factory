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

from lane_dialogue import run_engagement  # noqa: E402 - adjacent harness module

from factory_core.directive_authority import (  # noqa: E402 - repository package
    Authorization,
    DirectiveAuthority,
    DirectiveAuthorityError,
    Utterance,
    authorizes,
    derive,
    strongest,
)
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



def _cited_authority(root: pathlib.Path, ref: str) -> DirectiveAuthority | None:
    """One cited authority, or None when the citation cannot be checked.

    The format is deliberately explicit rather than a bare id: a citation that
    does not carry its speaker and its transcript digest cannot be told apart
    from a lane's assertion, which is the whole failure this addresses.
    """

    # The digest itself carries a colon (`sha256:<hex>`), so a plain split(":", 3)
    # sliced it in half: the digest became "sha256" and `surfaced` became the hex
    # plus the real flag, which is truthy — turning an intended UNILATERAL into
    # DISCLOSED and an intended escalation into a silent proceed. Parse the two
    # fixed fields off the front and the flag off the back; the digest is whatever
    # is between, colons and all.
    head = ref.split(":", 2)
    if len(head) != 3:
        return None
    speaker, position, rest = head
    if ":" not in rest:
        return None
    digest, surfaced = rest.rsplit(":", 1)
    if not digest:
        return None
    try:
        utterance = Utterance(
            utterance_id=ref,
            speaker=speaker,
            position=int(position),
            line_digest=digest,
            surfaced=surfaced.strip().lower() not in {"0", "false", "no"},
        )
    except (DirectiveAuthorityError, ValueError):
        return None
    # No transcript is threaded here, so engagement is not observable and the
    # derivation degrades to DISCLOSED for a surfaced lane statement. That is the
    # fail-closed direction: a lane cannot earn ENGAGED by asserting it.
    return derive(utterance)


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

    # Who asked for this decides whether the run may take it without asking.
    # `route()` answers "should this be a question?" from determinability,
    # ownership and engagement; it never asked "does the human already know?".
    # A cited authority answers that, and it can only NARROW — a weak authority
    # turns a proceed into an escalation, never the reverse.
    authority_row: dict[str, object] | None = None
    cited = tuple(ref for ref in (arguments.authority or ()) if ref.strip())
    if cited:
        authorities = tuple(
            _cited_authority(root, ref) for ref in cited
        )
        binding = strongest(a for a in authorities if a is not None)
        if binding is None:
            print(
                "engagement-route refused: every cited authority is unresolvable; an "
                "uncheckable citation is a claim, not permission",
                file=sys.stderr,
            )
            return 64
        warrant: Authorization = authorizes(
            binding,
            reversibility=item.reversibility,
            critical=item.critical,
            determinable=item.determinable,
        )
        authority_row = {
            "utterance_id": binding.utterance_id,
            "speaker": binding.speaker,
            "tier": binding.tier,
            "basis": binding.basis,
            "line_digest": binding.line_digest,
            "outcome": warrant.outcome,
            "reason": warrant.reason,
        }
        if not warrant.allowed and routing.action not in (BLOCK_FOR_HUMAN, NOTIFY_CHANNEL):
            # Monotone: the authority is too weak for this action, so what would
            # have proceeded silently becomes an escalation instead.
            routing = Routing(RECORD_ESCALATION, warrant.reason)

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
    if authority_row is not None:
        record["authority"] = authority_row
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
    item.add_argument(
        "--authority",
        action="append",
        default=[],
        help=(
            "utterance id(s) cited as authority for proceeding, as "
            "<speaker>:<position>:<line-digest>:<surfaced>. The tier is DERIVED; "
            "the strongest cited authority binds and can only narrow the routing."
        ),
    )

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
