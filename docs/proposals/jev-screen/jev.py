"""Minimal stdlib client for TypeSafe /v1/systemone (scratch prototype)."""
import json, os, re, time, urllib.request, urllib.error

def _key():
    k = os.environ.get("TYPESAFE_API_KEY")
    if not k:
        raise SystemExit("set TYPESAFE_API_KEY")
    return k


KEY = _key()

def ask(state, questions, model="jev-1.13.0", retries=4):
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    for attempt in range(retries):
        req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=body,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
                out["_latency_ms"] = round((time.time() - t0) * 1000)
                return out
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < retries - 1:
                time.sleep(2 ** attempt); continue
            raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:800]}")
