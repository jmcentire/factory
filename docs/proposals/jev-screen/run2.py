"""Robustness: {original, heldout} cases x {full criteria, criteria without examples}."""
import copy, re, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/Users/jmcentire/Code/factory/harness")
import dispatcher as D
import rules as R, cases as C, heldout as H
from jev import ask

def strip(q):
    q = copy.deepcopy(q)
    crit = q["criteria"]
    for v in crit.values():
        if isinstance(v, dict):
            v.pop("examples", None)
    return q

def rx(p, t): return bool(re.search(p, t, re.IGNORECASE))

def jobs_for(M, heldout):
    J = []
    for c in M.CONTAMINATION:
        cid, lane, text, label = (c[0], c[1], c[2], c[3]) if heldout else (c[0], c[2], c[3], c[4])
        J.append(("contamination", cid, {"lane": lane, "pane_excerpt": text}, R.CONTAMINATION, label, rx(D.TRIGGER_PATTERNS["contamination"], text)))
    for c in M.LANE_STATE:
        cid, lane, text, label = (c[0], c[1], c[2], c[3]) if heldout else (c[0], c[2], c[3], c[4])
        J.append(("lane_state", cid, {"lane": lane, "pane_tail": text}, R.LANE_STATE, label, None))
    for c in M.PEN_PICKUP:
        cid, text, label = (c[0], c[1], c[2]) if heldout else (c[0], c[2], c[3])
        J.append(("pen_pickup", cid, {"lane": "validator", "pane_excerpt": text}, R.PEN_PICKUP, label, None))
    for c in M.PROMISE:
        cid, text, label = (c[0], c[1], c[2]) if heldout else (c[0], c[2], c[3])
        J.append(("promise", cid, {"speaker": "validator", "line": text}, R.PROMISE, label, bool(D.detect_promises(text))))
    for c in M.AUTHORITY_CLAIM:
        cid, text, label = (c[0], c[1], c[2]) if heldout else (c[0], c[2], c[3])
        J.append(("authority", cid, {"speaker": "validator", "line": text}, R.AUTHORITY_CLAIM, label, bool(D.detect_authority_claims(text))))
    for c in M.DROPPED_QUALIFIER:
        cid, fv, rel, label = (c[0], c[1], c[2], c[3]) if heldout else (c[0], c[2], c[3], c[4])
        J.append(("dropped_qualifier", cid, {"founder_verbatim": fv, "relayed": rel}, R.DROPPED_QUALIFIER, label, None))
    return J

def decide(a):
    return a["noul"] > R.NOUL_YES if a["type"] == "noul" else a["choice"]

def val(a):
    return a["noul"] if a["type"] == "noul" else a["confidence"]

for setname, M, ho in (("original", C, False), ("heldout", H, True)):
    for variant in ("full", "no_examples"):
        J = jobs_for(M, ho)
        def go(j):
            q = j[3] if variant == "full" else strip(j[3])
            return j, ask(j[2], {"q": q})["answers"]["q"]
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(go, J))
        ok = sum(decide(a) == j[4] for j, a in res)
        rgx = [(j, a) for j, a in res if j[5] is not None]
        rok = sum(j[5] == j[4] for j, a in rgx)
        band = sum(1 for j, a in res if a["type"] == "noul" and 0.3 < a["noul"] < 0.7 or a["type"] != "noul" and a["confidence"] < 0.7)
        print(f"== {setname:8} {variant:11} jev {ok}/{len(res)}   regex {rok}/{len(rgx)}   in-uncertain-band {band}")
        for j, a in res:
            if decide(a) != j[4] or (a["type"] == "noul" and 0.3 < a["noul"] < 0.7) or (a["type"] != "noul" and a["confidence"] < 0.7):
                print(f"   {'XX' if decide(a)!=j[4] else '~~'} {j[0]:17} {j[1]:6} label={j[4]} jev={decide(a)} val={val(a)}")
