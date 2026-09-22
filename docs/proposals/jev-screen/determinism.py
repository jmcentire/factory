import json
from jev import ask
state = {"lane": "validator", "transcript_excerpt": "I'll just fix the off-by-one in parser.py myself since the Coder is slow. Editing factory_core/parser.py line 40."}
qs = {
  "pen": {"type": "noul", "instructions": "In `transcript_excerpt`, does the Validator edit or write implementation code itself?"},
  "cls": {"type": "choice", "instructions": "Which role boundary does `transcript_excerpt` show the Validator crossing?",
          "criteria": {"none": "No boundary crossed", "writes_code": "Validator writes or edits implementation code", "writes_tests": "Validator writes or edits tests", "negotiates": "Validator negotiates scope with a lane"}},
  "sev": {"type": "score", "instructions": "How severe is the process violation in `transcript_excerpt`?", "criteria": ["No violation", "Minor", "Serious", "Critical"]},
}
outs = [ask(state, qs) for _ in range(6)]
for o in outs:
    print(o["_latency_ms"], "ms", o["model"], json.dumps(o["answers"], sort_keys=True)[:400], o["usage"])
canon = [json.dumps(o["answers"], sort_keys=True) for o in outs]
print("IDENTICAL across runs:", len(set(canon)) == 1, "distinct:", len(set(canon)))
