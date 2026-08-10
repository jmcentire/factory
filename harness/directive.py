#!/usr/bin/env python3
"""directive.py — append-only, hash-chained, verbatim directive ledger.

Enforces (fucked_up.md §8-§12):
  * verbatim text only; there is no edit — corrections arrive as new entries
  * qualifiers are first-class; superseding forces an explicit disposition
    (kept / dropped / modified) for EVERY qualifier the parent carried
  * verify recomputes the chain; with --sigs it also requires the ledger's
    git history to be signature-clean (hardware key on the founder's side:
    the agent can verify, it cannot sign)

Agents may run: verify, show, active, provisional. The signed chain stays
founder-only — appends the founder didn't sign fail verification. Live rulings
enter through the provisional side chain (provisional.jsonl): agent-appendable,
transcript-cited, TTL'd, settled later by signed ratify/refuse entries.
"""
import argparse, datetime, hashlib, json, os, pathlib, subprocess, sys

LEDGER = pathlib.Path(os.environ.get("DIRECTIVE_LEDGER", "DIRECTIVES/ledger.jsonl"))
PROV = LEDGER.with_name("provisional.jsonl")
GENESIS = "0" * 64

def canon(d): return json.dumps(d, sort_keys=True, separators=(",", ":"))
def ehash(body): return hashlib.sha256(canon(body).encode()).hexdigest()

def load():
    if not LEDGER.exists(): return []
    out = []
    for i, line in enumerate(LEDGER.read_text().splitlines(), 1):
        if line.strip():
            try: out.append(json.loads(line))
            except json.JSONDecodeError: sys.exit(f"line {i}: not JSON — ledger corrupt, stop")
    return out

def loadp():
    if not PROV.exists(): return []
    return [json.loads(l) for l in PROV.read_text().splitlines() if l.strip()]

def verify(sigs=False):
    prev, entries = GENESIS, load()
    for e in entries:
        body = {k: v for k, v in e.items() if k != "hash"}
        if body.get("prev_hash") != prev: sys.exit(f"{e.get('id')}: chain broken")
        if ehash(body) != e.get("hash"): sys.exit(f"{e.get('id')}: content altered")
        prev = e["hash"]
    prevp = GENESIS
    for p in loadp():
        bodyp = {k: v for k, v in p.items() if k != "hash"}
        if bodyp.get("prev_hash") != prevp: sys.exit(f"{p.get('id')}: provisional chain broken")
        if ehash(bodyp) != p.get("hash"): sys.exit(f"{p.get('id')}: provisional altered")
        prevp = p["hash"]
    if sigs and (LEDGER.parent / ".git").exists():
        r = subprocess.run(["git", "-C", str(LEDGER.parent), "log",
                            "--format=%H %G? %GS", "--", LEDGER.name],
                           capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() if l.split()[1:2] != ["G"]]
        if bad: sys.exit("unsigned/badly-signed ledger commits:\n" + "\n".join(bad))
    print(f"ok: {len(entries)} signed + {len(loadp())} provisional, head={prev[:12]}")

def write(body):
    body["hash"] = ehash(body)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f: f.write(canon(body) + "\n")
    print(body["id"], body["hash"][:12])

def new_body(entries, args, supersedes=None, dispositions=None, quals=None):
    return {"id": f"D-{len(entries)+1:04d}",
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "scope": args.scope, "text": args.text,
            "qualifiers": quals if quals is not None else (args.qualifier or []),
            "supersedes": supersedes, "dispositions": dispositions,
            "prev_hash": entries[-1]["hash"] if entries else GENESIS}

def cmd_append(args): write(new_body(load(), args))

def cmd_supersede(args):
    entries = load()
    parent = next((e for e in entries if e["id"] == args.parent), None)
    if not parent: sys.exit(f"no such directive: {args.parent}")
    if any(e.get("supersedes") == args.parent for e in entries):
        sys.exit(f"{args.parent} already superseded")
    disp = {}
    for s in args.set or []:
        parts = s.split("::", 2)
        if len(parts) < 2: sys.exit(f"bad --set (QUAL::kept|dropped|modified::new): {s}")
        disp[parts[0]] = {"action": parts[1], "new": parts[2] if len(parts) > 2 else None}
    missing = [q for q in parent.get("qualifiers", []) if q not in disp]
    if missing:
        sys.exit("supersession refused — undispositioned qualifiers "
                 "(this is the control; every qualifier gets kept/dropped/modified):\n  - "
                 + "\n  - ".join(missing))
    quals = [q for q in parent.get("qualifiers", []) if disp[q]["action"] == "kept"]
    quals += [d["new"] for d in disp.values() if d["action"] == "modified" and d["new"]]
    quals += (args.qualifier or [])
    write(new_body(entries, args, supersedes=args.parent, dispositions=disp, quals=quals))

def cmd_provisional(args):
    # Agent-appendable side chain — NOT the signed ledger. Authority is borrowed
    # from the live transcript, cited precisely, and expires unless ratified.
    entries = loadp()
    now = datetime.datetime.now(datetime.timezone.utc)
    body = {"id": f"P-{len(entries)+1:04d}",
            "ts": now.isoformat(timespec="seconds"),
            "scope": args.scope, "text": args.text,
            "qualifiers": args.qualifier or [],
            "cite": args.cite,   # transcript file:line:uuid:line-sha256
            "expires": (now + datetime.timedelta(hours=args.ttl_hours)).isoformat(timespec="seconds"),
            "prev_hash": entries[-1]["hash"] if entries else GENESIS}
    body["hash"] = ehash(body)
    PROV.parent.mkdir(parents=True, exist_ok=True)
    with open(PROV, "a") as f: f.write(canon(body) + "\n")
    print(body["id"], body["hash"][:12], "expires", body["expires"])

def cmd_ratify(args):
    prov = next((p for p in loadp() if p["id"] == args.pid), None)
    if not prov: sys.exit(f"no such provisional: {args.pid}")
    entries = load()
    verdict = "refuses" if args.refuse else "ratifies"
    body = {"id": f"D-{len(entries)+1:04d}",
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "scope": prov["scope"],
            "text": args.text if args.text else prov["text"],
            "qualifiers": prov["qualifiers"],
            "supersedes": None, "dispositions": None,
            verdict: {"id": prov["id"], "hash": prov["hash"], "cite": prov["cite"]},
            "prev_hash": entries[-1]["hash"] if entries else GENESIS}
    write(body)
    if args.refuse:
        print(f"refused after action — artifacts citing {prov['id']} are [AGENT]-originated:")
        print("freeze them and route each for an explicit keep/revert disposition")

def cmd_active(args):
    entries = load()
    dead = {e["supersedes"] for e in entries if e.get("supersedes")}
    for e in entries:
        if e["id"] in dead: continue
        if args.since and e["ts"] < args.since: continue
        print(f'{e["id"]} [{e["scope"]}] {e["text"]}')
        for q in e.get("qualifiers", []): print(f"      ↳ {q}")
    settled = set()
    for e in entries:
        for k in ("ratifies", "refuses"):
            if e.get(k): settled.add(e[k]["id"])
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    for p in loadp():
        if p["id"] in settled or p["expires"] < now: continue
        if args.since and p["ts"] < args.since: continue
        print(f'{p["id"]} [PROVISIONAL until {p["expires"]}] [{p["scope"]}] {p["text"]}  <- {p["cite"]}')
        for q in p.get("qualifiers", []): print(f"      ↳ {q}")

def cmd_show(args):
    for e in load():
        if e["id"] == args.id: print(json.dumps(e, indent=2)); return
    sys.exit(f"no such directive: {args.id}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append"); a.add_argument("--scope", required=True)
    a.add_argument("--text", required=True); a.add_argument("--qualifier", action="append")
    a.set_defaults(f=cmd_append)
    s = sub.add_parser("supersede"); s.add_argument("parent")
    s.add_argument("--scope", required=True); s.add_argument("--text", required=True)
    s.add_argument("--set", action="append"); s.add_argument("--qualifier", action="append")
    s.set_defaults(f=cmd_supersede)
    v = sub.add_parser("verify"); v.add_argument("--sigs", action="store_true")
    v.set_defaults(f=lambda a: verify(a.sigs or os.environ.get("DIRECTIVE_REQUIRE_SIGS") == "1"))
    pr = sub.add_parser("provisional"); pr.add_argument("--scope", required=True)
    pr.add_argument("--text", required=True); pr.add_argument("--cite", required=True)
    pr.add_argument("--qualifier", action="append"); pr.add_argument("--ttl-hours", type=int, default=72)
    pr.set_defaults(f=cmd_provisional)
    ra = sub.add_parser("ratify"); ra.add_argument("pid")
    ra.add_argument("--refuse", action="store_true"); ra.add_argument("--text", default=None)
    ra.set_defaults(f=cmd_ratify)
    ac = sub.add_parser("active"); ac.add_argument("--since", default=None)
    ac.set_defaults(f=cmd_active)
    sh = sub.add_parser("show"); sh.add_argument("id"); sh.set_defaults(f=cmd_show)
    args = p.parse_args(); args.f(args)
