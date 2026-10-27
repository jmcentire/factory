"""Keep policy-denial holds outside the model's writable and executable context.

This is the existing custody service's evaluation path, never an execution path. Signet
still decides each tool request. A non-prerequisite refusal is durable before its response
returns; changing the next request cannot erase it. Only the host can release a hold against
an accepted, later Orchestrator assessment. Payloads and evaluator output are not journaled.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import os
import pathlib
import re
import stat
from typing import Any

import lane_custody as custody
import orchestrator_channel
import signet_hooks
from attention_gate import check_lane_admission

_SHA = re.compile(r"[0-9a-f]{64}\Z")


def validate(value: Any, home: pathlib.Path) -> dict[str, Any]:
    fields = {"client", "contract", "contract_digest", "root", "sources", "prerequisite"}
    if not isinstance(value, dict) or set(value) != fields:
        raise custody.CustodyError("invalid host policy binding")
    if not isinstance(value["client"], str) or value["client"] not in signet_hooks.CLIENTS:
        raise custody.CustodyError("unsupported bound policy client")
    for key in ("contract", "root"):
        custody._absolute(value[key])
    signet_hooks.contract(pathlib.Path(value["contract"]), value["contract_digest"])
    sources = value["sources"]
    if not isinstance(sources, dict) or set(sources) != {
            "harness/policy_custody.py", "harness/signet_hooks.py"}:
        raise custody.CustodyError("policy implementation is not pinned")
    for name, digest in sources.items():
        source = home / name
        if (source.is_symlink() or not source.is_file()
                or hashlib.sha256(source.read_bytes()).hexdigest() != digest):
            raise custody.CustodyError("host policy implementation changed")
    prerequisite = value["prerequisite"]
    if prerequisite is not None and (
            not isinstance(prerequisite, dict)
            or set(prerequisite) != {"policy_revision", "reason"}
            or any(not isinstance(v, str) or not v.strip() for v in prerequisite.values())):
        raise custody.CustodyError("prerequisite must bind the exact owner revision and reason")
    return value


@contextlib.contextmanager
def locked(receipts: pathlib.Path):
    path = receipts / "policy.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise custody.CustodyError("invalid policy lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def holds(receipts: pathlib.Path) -> list[dict[str, Any]]:
    """A crash after started is also a hold; neither missing nor malformed is success."""
    result = []
    for index, path in enumerate(sorted(receipts.glob("policy-*.started.json"))):
        if index >= 65536:
            raise custody.CustodyError("policy receipt bound exceeded")
        start = custody._read_receipt(path)
        if (set(start) != {"request_digest", "seat", "at"}
                or not isinstance(start.get("request_digest"), str)
                or not _SHA.fullmatch(start["request_digest"])
                or not isinstance(start["at"], str)):
            raise custody.CustodyError("invalid policy start receipt")
        custody._identity(start["seat"])
        stem = path.name.removesuffix(".started.json")
        end = receipts / (stem + ".terminal.json")
        terminal = custody._read_receipt(end) if end.exists() else {}
        if terminal.get("request_digest") != start.get("request_digest"):
            terminal = {}
        if terminal.get("outcome") in {"allow", "ask", "prerequisite"}:
            continue
        release = receipts / (stem + ".released.json")
        if release.exists() or release.is_symlink():
            receipt = custody._read_receipt(release)
            if (set(receipt) == {"denial_id", "request_digest", "assessment_digest", "at"}
                    and receipt.get("denial_id") == stem
                    and receipt.get("request_digest") == start.get("request_digest")
                    and isinstance(receipt.get("assessment_digest"), str)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", receipt["assessment_digest"])
                    and isinstance(receipt.get("at"), str)):
                continue
            raise custody.CustodyError("policy release does not bind the retained denial")
        result.append({"denial_id": stem, **start, "outcome": terminal.get("outcome", "uncertain")})
    return result


def evaluate(plan: custody.Plan, request: Any, receipts: pathlib.Path) -> dict[str, Any]:
    if (not isinstance(request, dict) or set(request) != {
            "kind", "request_id", "plan_digest", "payload"}
            or request["kind"] != "policy"
            or not isinstance(request["request_id"], str)
            or not custody._ID.fullmatch(request["request_id"])
            or len(request["request_id"]) > 80
            or request["plan_digest"] != plan.digest):
        raise custody.CustodyError("invalid policy custody request")
    binding = validate(plan.document.get("policy"), pathlib.Path(plan.document["factory_home"]))
    client = binding["client"]
    fixed = signet_hooks.contract(pathlib.Path(binding["contract"]), binding["contract_digest"])
    with locked(receipts):
        if holds(receipts):
            return {"status": "policy", "output": signet_hooks.denial(
                client, "Factory policy hold: supervised disposition required; do not retry "
                "or re-express the denied operation.")}
        stem = "policy-" + request["request_id"]
        start = {"request_digest": custody._digest(request), "seat": plan.document["seat"],
                 "at": datetime.datetime.now(datetime.UTC).isoformat()}
        custody._save_once(receipts / (stem + ".started.json"), start)
        # An evaluator failure leaves the started receipt unresolved and therefore held.
        output, health = signet_hooks.evaluate_with_health(client, request["payload"], fixed)
        specific = output.get("hookSpecificOutput", output)
        decision = specific.get("permissionDecision", specific.get("permission"))
        reason = specific.get("permissionDecisionReason", specific.get("agent_message", ""))
        outcome = decision
        prerequisite = binding["prerequisite"]
        if decision == "deny" and prerequisite is not None and (
                health.get("policy_revision") == prerequisite["policy_revision"]
                and reason == prerequisite["reason"]):
            # This is an owner-pinned recovery instruction, not an ALLOW or evidence of
            # completed retrieval. The original denial is returned unchanged; Kindex must
            # actually be called and Signet must independently permit the next action.
            outcome = "prerequisite"
        if outcome not in {"allow", "ask", "deny", "prerequisite"}:
            raise custody.CustodyError("unknown policy outcome")
        custody._save_once(receipts / (stem + ".terminal.json"), {
            **start, "outcome": outcome, "reason_digest": custody._digest(reason),
            "policy_revision": health["policy_revision"]})
        if outcome == "deny":
            cursor = orchestrator_channel.append_activity(
                pathlib.Path(binding["root"]), kind="deterministic_signal", source="dispatcher",
                detail=f"Host policy custody hold {stem}; seat {plan.document['seat']['pid']}; "
                f"request digest {start['request_digest']}. Original Signet denial retained. "
                "Further tool calls and custody operations are held until a supervised host "
                "disposition. No raw tool input or evaluator output is logged.")
            custody._save_once(receipts / (stem + ".notice.json"), {"cursor": cursor})
        return {"status": "policy", "output": output}


def release(plan: custody.Plan, receipts: pathlib.Path, denial_id: str,
            assessment_digest: str) -> dict[str, Any]:
    """Host-only CLI; there is deliberately no socket request for releasing a hold."""
    if not denial_id.startswith("policy-") or not custody._ID.fullmatch(denial_id):
        raise custody.CustodyError("invalid retained denial ID")
    binding = validate(plan.document.get("policy"), pathlib.Path(plan.document["factory_home"]))
    with locked(receipts):
        pending = {item["denial_id"]: item for item in holds(receipts)}
        if denial_id not in pending:
            raise custody.CustodyError("denial is not pending")
        root = pathlib.Path(binding["root"])
        notice_path = receipts / (denial_id + ".notice.json")
        if not notice_path.exists() and not notice_path.is_symlink():
            # A crash or notification failure must remain recoverable without pretending
            # the Orchestrator saw it. Publish the missing notice, then require review.
            cursor = orchestrator_channel.append_activity(
                root, kind="deterministic_signal", source="dispatcher",
                detail=f"Unresolved host policy custody hold {denial_id}; "
                "evaluation or notice delivery was incomplete. Supervised review required.")
            custody._save_once(notice_path, {"cursor": cursor})
            raise custody.CustodyError(
                "hold notice recorded; await current assessment before release")
        notice = custody._read_receipt(notice_path)
        if type(notice.get("cursor")) is not int or notice["cursor"] < 1:
            raise custody.CustodyError("invalid policy notice")
        # Use canonical channel validation; an arbitrary JSON or stale/no-longer-current
        # no-op is not an accepted supervision disposition.
        orchestrator_channel.require_current(root)
        # A later cadence no-op can coexist with an earlier unresolved BLOCK. Use the
        # existing receipted attention gate, not the latest report's label alone.
        check_lane_admission(root, "validator")
        rows = orchestrator_channel._read_jsonl(root / "orchestrator/reports.jsonl")
        report = rows[-1] if rows else {}
        assessment = report.get("assessment", {})
        if (report.get("assessment_digest") != assessment_digest
                or assessment.get("decision") != "no-op"
                or assessment.get("through_cursor", 0) < notice["cursor"]):
            raise custody.CustodyError("current assessment does not dispose of this denial")
        result = {"denial_id": denial_id,
                  "request_digest": pending[denial_id]["request_digest"],
                  "assessment_digest": assessment_digest,
                  "at": datetime.datetime.now(datetime.UTC).isoformat()}
        custody._save_once(receipts / (denial_id + ".released.json"), result)
        return result
