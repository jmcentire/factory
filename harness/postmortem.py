#!/usr/bin/env python3
"""postmortem.py — a postmortem is derived from primary sources or it is fiction.

Reads ONLY the run's recorded artifacts: authoritative run.json, harness.json, events.jsonl,
injections.jsonl, wakes/, the receipt chain, and lane session usage where locatable.
Every number in the output carries its derivation; a value with no primary source is
rendered UNDERIVED rather than estimated — the 5-vs-96 counting error was an agent
reporting the size of its context window as the size of the world (fucked_up §11.1).

Per-agent feedback is COLLECTED by the Validator (each lane answers in its own
window before teardown); this script reserves the sections and refuses to invent
their content. Cost splits coordination vs build so the postmortem can arbitrate
the persistent-lanes-at-small-scale question over iterations.
"""
import argparse
import datetime
import json
import pathlib
import sys
from typing import Any


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def lane_usage(workspace: pathlib.Path, agent: str) -> dict[str, Any]:
    """Best-effort token/cost recovery from Claude Code session logs for a lane
    workspace. Absence yields UNDERIVED, never an estimate."""
    if agent != "claude":
        return {
            "tokens": f"UNDERIVED (no {agent} usage adapter; PR2 qualification gap)",
            "source": None,
        }
    slug = str(workspace.resolve()).replace("/", "-")
    projects = pathlib.Path.home() / ".claude" / "projects" / slug
    if not projects.is_dir():
        return {"tokens": "UNDERIVED (no session log found)", "source": None}
    tokens = 0
    files = sorted(projects.glob("*.jsonl"))
    for f in files:
        for line in f.read_text(errors="replace").splitlines():
            if '"usage"' not in line:
                continue
            try:
                u = json.loads(line).get("message", {}).get("usage", {})
                tokens += int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return {"tokens": tokens, "source": f"{projects} ({len(files)} session files)"}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, help=".factory/runs/<run>")
    args = p.parse_args()
    root = pathlib.Path(args.root)
    run_file = root / "run.json"
    if not run_file.exists():
        sys.exit(f"no run.json under {root} — nothing to derive from")
    run = json.loads(run_file.read_text())
    harness_file = root / "harness.json"
    harness = json.loads(harness_file.read_text()) if harness_file.exists() else {}

    events = read_jsonl(root / "events.jsonl")
    dispatches = read_jsonl(root / "dispatches.jsonl")
    injections = read_jsonl(root / "injections.jsonl")
    wakes = read_jsonl(root / "wakes" / "receipts.jsonl")
    receipts = read_jsonl(root.parent.parent / "receipts" / "chain.jsonl")
    verdict_file = root / "endgame" / "verdict.json"
    verdict = json.loads(verdict_file.read_text()) if verdict_file.exists() else None

    by_kind: dict[str, int] = {}
    for e in events:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1

    lanes = sorted({d.get("role", "?") for d in dispatches}) or ["(none dispatched)"]
    coord = [i for i in injections if i.get("from") in ("dispatcher", "orchestrator")]
    build = [i for i in injections if i.get("from") == "validator"]

    lines: list[str] = []
    w = lines.append
    run_id = str(run.get("run_id") or run.get("run") or "UNDERIVED")
    w(f"# Postmortem — run `{run_id}`")
    w("")
    w(f"Generated {datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')} "
      f"from primary sources under `{root}`. Numbers without a source say UNDERIVED.")
    w("")
    w("## Run")
    target = run.get("target_state", {})
    w(
        f"- exact commit: `{target.get('resolved_commit', 'UNDERIVED')}` "
        "(checked run.json target-state)"
    )
    w(f"- target-state digest: `{run.get('target_state_digest', 'UNDERIVED')}` (run.json)")
    w(
        f"- source/workdir: `{target.get('source_root', 'UNDERIVED')}` / "
        f"`{target.get('workdir', 'UNDERIVED')}`"
    )
    w(
        f"- task digest: `{harness.get('task_digest', 'UNDERIVED')}` "
        "(harness.json; bound to Stage E)"
    )
    w(f"- declared budget: {harness.get('budget_usd') or 'none declared'}; "
      f"enforcement={harness.get('budget_enforcement', 'UNQUALIFIED')}")
    w(f"- launcher/isolation: {harness.get('launcher_qualification', 'UNQUALIFIED')} / "
      f"{harness.get('lane_isolation', 'UNQUALIFIED')}")
    endgame = (
        verdict["verdict"] + " (endgame/verdict.json)"
        if verdict
        else "UNDERIVED (endgame never ran)"
    )
    w(f"- endgame verdict: {endgame}")
    w("")
    w("## Derived counts (source: events.jsonl / dispatches.jsonl / injections.jsonl / wakes)")
    event_counts = ", ".join(f"{key}={value}" for key, value in sorted(by_kind.items()))
    w(f"- events: {len(events)} total — {event_counts}")
    w(f"- lane dispatches: {len(dispatches)} ({', '.join(lanes)})")
    w(f"- injections: {len(injections)} total; validator→lanes {len(build)}, "
      f"coordination (dispatcher/orchestrator→validator) {len(coord)}")
    w(f"- orchestrator wakes: {len(wakes)} (projection receipts in wakes/receipts.jsonl)")
    w(f"- receipts in chain: {len(receipts)} (.factory/receipts/chain.jsonl)")
    w("")
    # Silent-clear detection: every blocking_written should have a matching
    # blocking_consumed (consume_block.sh receipts the event it cleared). A
    # blocking_written with no matching blocking_consumed means the .blocking file
    # was rm'd/truncated without the off-ramp — the attention signal was lost, not
    # consumed. This is the "clearing-without-reading is visible by its absence"
    # guarantee the blocking channel exists to provide.
    written = [e for e in events if e.get("kind") == "blocking_written"]
    consumed = [e for e in events if e.get("kind") == "blocking_consumed"]
    consumed_keys = {json.dumps(c.get("event"), sort_keys=True) for c in consumed}
    silent = [e for e in written
              if json.dumps(e.get("event"), sort_keys=True) not in consumed_keys]
    w("## Attention channel integrity (source: events.jsonl)")
    w(f"- blocking events written: {len(written)}; consumed via off-ramp: {len(consumed)}")
    if silent:
        w(f"- SILENT CLEARS: {len(silent)} blocking event(s) written but never consumed — "
          f"the .blocking file was cleared without consume_block.sh, so the attention "
          f"signal was lost, not consumed. Lanes: "
          + ", ".join(sorted({s.get("lane", "?") for s in silent})))
    else:
        w("- no silent clears: every blocking event written was consumed via the off-ramp")
    w("")
    w("## Spend per lane (source: Claude Code session logs; absence = UNDERIVED)")
    for d in dispatches:
        ws = d.get("projection", {}).get("dest", "")
        u = (
            lane_usage(pathlib.Path(ws), str(d.get("agent", "unknown")))
            if ws
            else {"tokens": "UNDERIVED", "source": None}
        )
        w(f"- {d.get('role')}: tokens={u['tokens']}" + (f" — {u['source']}" if u["source"] else ""))
    validator_workdir = pathlib.Path(str(target.get("workdir", ".")))
    vu = lane_usage(validator_workdir, "claude")
    validator_source = f" — {vu['source']}" if vu["source"] else ""
    w(f"- validator (exact target workdir): tokens={vu['tokens']}{validator_source}")
    w("")
    w("## Coordination vs build")
    w("Coordination = dispatcher/orchestrator traffic + wakes; build = validator→lane "
      "dispatch and tending. This split arbitrates the persistent-lanes-at-small-scale "
      "question across iterations (coder postmortem: coordination exceeded build once).")
    w(f"- coordination signals: {len(coord) + len(wakes)}")
    w(f"- build signals: {len(build) + len(dispatches)}")
    w("")
    w("## Per-agent feedback (collected by the Validator before teardown — never invented here)")
    for lane in ("validator", "coder", "tester", "orchestrator"):
        w(f"### {lane}")
        fb = root / "feedback" / f"{lane}.md"
        if fb.exists():
            w(fb.read_text().strip())
        else:
            w(f"UNCOLLECTED — have the {lane} answer: what the harness got in its way, "
              "what it supplied for itself that the harness should own, what it would "
              "change in the next iteration.")
        w("")
    w("## Corrections pass")
    w("Diff this document against the raw pane logs and receipts before treating any "
      "conclusion as settled; superseded conclusions are marked FALSE-AS-WRITTEN and "
      "kept, never rewritten. Proposed process changes escalate through the Diff-Intent "
      "Gate and trigger requalification — they do not silently edit the next run.")

    out = root / "postmortem.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"postmortem written: {out}")


if __name__ == "__main__":
    main()
