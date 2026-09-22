"""Run the rulebook against the cases; compare with the dispatcher's live regexes."""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/jmcentire/Code/factory/harness")
import dispatcher as D  # the real, current patterns
import rules as R, cases as C
from jev import ask

REPS = 3
jobs = []  # (rule, id, src, state, question_key, question, label, regex_hit)

def rx(pat, text):
    return bool(re.search(pat, text, re.IGNORECASE))

for cid, src, lane, text, label in C.CONTAMINATION:
    jobs.append(("contamination", cid, src, {"lane": lane, "pane_excerpt": text}, R.CONTAMINATION, label,
                 rx(D.TRIGGER_PATTERNS["contamination"], text)))
for cid, src, lane, text, label in C.LANE_STATE:
    jobs.append(("lane_state", cid, src, {"lane": lane, "pane_tail": text}, R.LANE_STATE, label,
                 "blocked_on_question" if rx(D.TRIGGER_PATTERNS["blocking_question"], text) else "stall"))
for cid, src, text, label in C.PEN_PICKUP:
    jobs.append(("pen_pickup", cid, src, {"lane": "validator", "pane_excerpt": text}, R.PEN_PICKUP, label, None))
for cid, src, text, label in C.PROMISE:
    jobs.append(("promise", cid, src, {"speaker": "validator", "line": text}, R.PROMISE, label, bool(D.detect_promises(text))))
for cid, src, text, label in C.AUTHORITY_CLAIM:
    jobs.append(("authority", cid, src, {"speaker": "validator", "line": text}, R.AUTHORITY_CLAIM, label, bool(D.detect_authority_claims(text))))
for cid, src, fv, rel, label in C.DROPPED_QUALIFIER:
    jobs.append(("dropped_qualifier", cid, src, {"founder_verbatim": fv, "relayed": rel}, R.DROPPED_QUALIFIER, label, None))

def run(job):
    outs = [ask(job[3], {"q": job[4]}) for _ in range(REPS)]
    return job, outs

with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(run, jobs))

rows, tokens, lat = [], 0, []
for (rule, cid, src, state, q, label, regex_hit), outs in results:
    answers = [o["answers"]["q"] for o in outs]
    tokens += sum(o["usage"]["input_tokens"] for o in outs)
    lat += [o["_latency_ms"] for o in outs]
    if q["type"] == "noul":
        vals = [a["noul"] for a in answers]
        decisions = [v > R.NOUL_YES for v in vals]
        shown = f"noul={vals}"
    else:
        decisions = [a["choice"] for a in answers]
        vals = [a["confidence"] for a in answers]
        shown = f"choice={decisions[0]} conf={vals} p={answers[0]['probabilities']}"
    stable = len(set(decisions)) == 1
    rows.append(dict(rule=rule, id=cid, src=src, label=label, jev=decisions[0], stable=stable,
                     jev_ok=decisions[0] == label, regex=regex_hit,
                     regex_ok=None if regex_hit is None else regex_hit == label, detail=shown,
                     model=outs[0]["model"]))

json.dump(rows, open("results.json", "w"), indent=1)
for r in rows:
    flag = "OK " if r["jev_ok"] else "XX "
    rg = "" if r["regex"] is None else f" regex={'ok' if r['regex_ok'] else 'WRONG'}({r['regex']})"
    print(f"{flag}{r['rule']:18} {r['id']:4} {r['src']:5} label={str(r['label']):26} jev={str(r['jev']):26} stable={r['stable']}{rg}  {r['detail'][:150]}")

print("\n== summary")
for rule in dict.fromkeys(r["rule"] for r in rows):
    rs = [r for r in rows if r["rule"] == rule]
    j = sum(r["jev_ok"] for r in rs)
    line = f"{rule:18} n={len(rs):2}  jev {j}/{len(rs)}"
    if rs[0]["regex"] is not None:
        line += f"  regex {sum(bool(r['regex_ok']) for r in rs)}/{len(rs)}"
    line += f"  unstable-across-{REPS}-calls: {sum(not r['stable'] for r in rs)}"
    print(line)
print(f"model={rows[0]['model']} calls={len(results)*REPS} input_tokens={tokens} cost=${tokens*0.042/1e6:.5f} "
      f"latency p50={sorted(lat)[len(lat)//2]}ms p95={sorted(lat)[int(len(lat)*.95)]}ms")
