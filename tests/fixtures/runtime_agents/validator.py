from __future__ import annotations

import hashlib
import json
import os
import runpy
import sys
from pathlib import Path


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


spec_path = Path(os.environ["FACTORY_SPEC_PATH"])
spec_bytes = spec_path.read_bytes()
if _digest(spec_bytes) != os.environ["FACTORY_SPEC_DIGEST"]:
    raise SystemExit("Validator received the wrong spec bytes")
spec = json.loads(spec_bytes)
implementation = Path(os.environ["FACTORY_IMPLEMENTATION_DIR"])
tests = Path(os.environ["FACTORY_TEST_DIR"])
assertions = json.loads(
    (tests / "evidence" / "assertions.json").read_text(encoding="utf-8")
)

expected_claims = {
    item["criterion_id"]: item["backreference"] for item in spec["acceptance"]
}
actual_claims = {
    claim["claim_id"]: claim["backreference"] for claim in assertions["claims"]
}
if actual_claims != expected_claims:
    raise SystemExit("Tester assertions do not resolve to every authorized criterion")

sys.path.insert(0, str(implementation / "artifact"))
runpy.run_path(str(tests / "tests" / "acceptance_test.py"), run_name="__main__")

output = Path(os.environ["FACTORY_OUTPUT_DIR"])
(output / "verdict.json").write_text(
    json.dumps(
        {
            "passed": True,
            "spec_digest": _digest(spec_bytes),
            "criteria": sorted(expected_claims),
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
