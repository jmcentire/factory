from __future__ import annotations

import hashlib
import json
import os
import runpy
import sys
from pathlib import Path


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest_obj(value: object) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


input_path = Path(os.environ["FACTORY_BUILD_INPUT_PATH"])
input_bytes = input_path.read_bytes()
if _digest(input_bytes) != os.environ["FACTORY_BUILD_INPUT_DIGEST"]:
    raise SystemExit("Validator received the wrong build-input bytes")
build_input = json.loads(input_bytes)
for path_name, digest_name in (
    ("FACTORY_BUILD_PLAN_PATH", "FACTORY_BUILD_PLAN_SOURCE_DIGEST"),
    ("FACTORY_PATTERN_CATALOG_PATH", "FACTORY_PATTERN_CATALOG_SOURCE_DIGEST"),
):
    data = Path(os.environ[path_name]).read_bytes()
    if _digest(data) != os.environ[digest_name]:
        raise SystemExit("Validator received stale construction IR")

implementation = Path(os.environ["FACTORY_IMPLEMENTATION_DIR"])
tests = Path(os.environ["FACTORY_TEST_DIR"])
assertions = json.loads((tests / "evidence" / "assertions.json").read_text(encoding="utf-8"))
product = next(
    artifact
    for artifact in build_input["phase_artifacts"]
    if artifact["phase"] == "product-specification"
)
item = product["items"][0]
backreference = {
    "artifact_id": product["artifact_id"],
    "artifact_digest": _digest_obj(product),
    "item_id": item["item_id"],
    "intent_digest": _digest_obj({"canonical_statement": item["canonical_statement"]}),
}
expected_claims = {criterion_id: backreference for criterion_id in ("AC-1", "AC-2")}
actual_claims = {claim["claim_id"]: claim["backreference"] for claim in assertions["claims"]}
if actual_claims != expected_claims:
    raise SystemExit("Tester assertions do not resolve to every authorized criterion")

sys.path.insert(0, str(implementation / "artifact"))
runpy.run_path(str(tests / "tests" / "acceptance_test.py"), run_name="__main__")

output = Path(os.environ["FACTORY_OUTPUT_DIR"])
(output / "verdict.json").write_text(
    json.dumps(
        {
            "passed": True,
            "build_input_digest": _digest(input_bytes),
            "criteria": sorted(expected_claims),
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
