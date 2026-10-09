"""Run the msg-r2 rules against their cases with the three-band decision (needs TYPESAFE_API_KEY).

Like run.py, but every answer goes through rules.band: yes above 0.7, no below 0.3, escalate in
between. A case counts as correct only when its band matches its label; an escalation on a
labelled case is reported separately, because in a run it goes to the Orchestrator rather than
being decided here. Not run in CI: tests/test_jev_rulebook.py checks the rulebook's structure
without calling the API.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import cases_msg_r2 as C
import rules as R
from jev import ask

REPS = 3

jobs = []  # (rule_id, case_id, src, state, question, label)
for rid, cases in C.CASES.items():
    for cid, src, state, label in cases:
        jobs.append((rid, cid, src, state, R.BY_ID[rid]["rule"], label))


def run(job):
    return job, [ask(job[3], {"q": job[4]}, model=R.MODEL_ID) for _ in range(REPS)]


with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(run, jobs))

rows, tokens = [], 0
for (rid, cid, src, state, q, label), outs in results:
    vals = [o["answers"]["q"]["noul"] for o in outs]
    bands = [R.band(v, R.BY_ID[rid]["band"]) for v in vals]
    tokens += sum(o["usage"]["input_tokens"] for o in outs)
    want = "yes" if label else "no"
    rows.append(dict(rule=rid, id=cid, src=src, label=label, band=bands[0], stable=len(set(bands)) == 1,
                     ok=bands[0] == want, escalated=bands[0] == "escalate", noul=vals, model=outs[0]["model"]))

json.dump(rows, open("results_msg_r2.json", "w"), indent=1)
for r in rows:
    flag = "OK " if r["ok"] else ("~~ " if r["escalated"] else "XX ")
    print(f"{flag}{r['rule']:34} {r['id']:4} {r['src']:6} label={str(r['label']):5} band={r['band']:8} stable={r['stable']} noul={r['noul']}")

print("\n== summary")
for rid in C.CASES:
    rs = [r for r in rows if r["rule"] == rid]
    print(f"{rid:34} n={len(rs):2}  ok {sum(r['ok'] for r in rs)}/{len(rs)}  escalated {sum(r['escalated'] for r in rs)}"
          f"  wrong {sum(not r['ok'] and not r['escalated'] for r in rs)}  unstable {sum(not r['stable'] for r in rs)}")
print(f"model={rows[0]['model']} calls={len(results) * REPS} input_tokens={tokens} cost=${tokens * 0.042 / 1e6:.5f}")
