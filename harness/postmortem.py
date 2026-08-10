#!/usr/bin/env python3
"""postmortem.py — a postmortem is derived from primary sources or it is fiction.

Reads ONLY the run's recorded artifacts: run.json, events.jsonl, dispatches.jsonl,
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


def lane_usage(workspace: pathlib.Path) -> dict[str, Any]:
    """Best-effort token/cost recovery from Claude Code session logs for a lane
    workspace. Absence yields UNDERIVED, never an estimate."""
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
    p.add_argument("--root", required=True, help=".harness/runs/<run>")
    args = p.parse_args()
    root = pathlib.Path(args.root)
    run_file = root / "run.json"
    if not run_file.exists():
        sys.exit(f"no run.json under {root} — nothing to derive from")
    run = json.loads(run_file.read_text())

    events = read_jsonl(root / "events.jsonl")
    dispatches = read_jsonl(root / "dispatches.jsonl")
    injections = read_jsonl(root / "injections.jsonl")
    wakes = read_jsonl(root / "wakes" / "receipts.jsonl")
    receipts = read_jsonl(pathlib.Path(".harness/receipts/chain.jsonl"))
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
    w(f"# Postmortem — run `{run['run']}`")
    w("")
    w(f"Generated {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} "
      f"from primary sources under `{root}`. Numbers without a source say UNDERIVED.")
    w("")
    w("## Run")
    w(f"- base SHA: `{run['base_sha']}` (run.json)")
    w(f"- task digest: `{run['task_digest']}` (run.json; verbatim task in TASK.md)")
    w(f"- declared budget: {run.get('budget_usd') or 'none declared'} (run.json)")
    w(f"- endgame verdict: "
      f"{verdict['verdict'] + ' (endgame/verdict.json)' if verdict else 'UNDERIVED (endgame never ran)'}")
    w("")
    w("## Derived counts (source: events.jsonl / dispatches.jsonl / injections.jsonl / wakes)")
    w(f"- events: {len(events)} total — " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    w(f"- lane dispatches: {len(dispatches)} ({', '.join(lanes)})")
    w(f"- injections: {len(injections)} total; validator→lanes {len(build)}, "
      f"coordination (dispatcher/orchestrator→validator) {len(coord)}")
    w(f"- orchestrator wakes: {len(wakes)} (projection receipts in wakes/receipts.jsonl)")
    w(f"- receipts in chain: {len(receipts)} (.harness/receipts/chain.jsonl)")
    w("")
    w("## Spend per lane (source: Claude Code session logs; absence = UNDERIVED)")
    for d in dispatches:
        ws = d.get("projection", {}).get("dest", "")
        u = lane_usage(pathlib.Path(ws)) if ws else {"tokens": "UNDERIVED", "source": None}
        w(f"- {d.get('role')}: tokens={u['tokens']}" + (f" — {u['source']}" if u["source"] else ""))
    vu = lane_usage(pathlib.Path(run.get("repo", ".")))
    w(f"- validator (repo cwd): tokens={vu['tokens']}" + (f" — {vu['source']}" if vu["source"] else ""))
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
