from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest_obj(value: object) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


input_path = Path(os.environ["FACTORY_BUILD_INPUT_PATH"])
input_bytes = input_path.read_bytes()
if _digest(input_bytes) != os.environ["FACTORY_BUILD_INPUT_DIGEST"]:
    raise SystemExit("Tester received the wrong build-input bytes")
build_input = json.loads(input_bytes)
if "FACTORY_BUILD_PLAN_PATH" in os.environ or "FACTORY_PATTERN_CATALOG_PATH" in os.environ:
    raise SystemExit("Tester received Coder-only construction IR")

coder_sentinel = Path.cwd().parent.parent / "coder" / "private" / "sentinel.txt"
try:
    coder_sentinel.read_text(encoding="utf-8")
except OSError:
    cross_lane_read_denied = True
else:
    raise SystemExit("Tester could read the Coder lane")

artifacts = {item["phase"]: item for item in build_input["phase_artifacts"]}
product = artifacts["product-specification"]
architecture = artifacts["architecture"]
product_item = product["items"][0]
if "adds integers" not in product_item["canonical_statement"].lower():
    raise SystemExit("synthetic Tester cannot derive the requested outcome")
if "calculator.py:add" not in architecture["items"][0]["canonical_statement"]:
    raise SystemExit("synthetic Tester cannot derive the public interface")
backreference = {
    "artifact_id": product["artifact_id"],
    "artifact_digest": _digest_obj(product),
    "item_id": product_item["item_id"],
    "intent_digest": _digest_obj({"canonical_statement": product_item["canonical_statement"]}),
}
examples = (
    ("AC-1", 2, 3, 5),
    ("AC-2", -7, 4, -3),
)
test_source = [
    "from __future__ import annotations",
    "",
    "from calculator import add",
    "",
]
for _criterion_id, left, right, expected in examples:
    test_source.extend(
        (
            f"# authority: {backreference['artifact_digest']} {backreference['item_id']}",
            f"assert add({left!r}, {right!r}) == {expected!r}",
            "",
        )
    )

output = Path(os.environ["FACTORY_OUTPUT_DIR"])
(output / "tests").mkdir()
(output / "evidence").mkdir()
(output / "tests" / "acceptance_test.py").write_text(
    "\n".join(test_source),
    encoding="utf-8",
)
(output / "evidence" / "assertions.json").write_text(
    json.dumps(
        {
            "claims": [
                {
                    "claim_id": criterion_id,
                    "kind": "test-assertion",
                    "backreference": backreference,
                }
                for criterion_id, *_ in examples
            ]
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
(output / "evidence" / "lane-evidence.json").write_text(
    json.dumps(
        {
            "role": "tester",
            "build_input_digest": _digest(input_bytes),
            "construction_ir_absent": True,
            "cross_lane_read_denied": cross_lane_read_denied,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
