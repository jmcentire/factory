from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


input_path = Path(os.environ["FACTORY_BUILD_INPUT_PATH"])
input_bytes = input_path.read_bytes()
if _digest(input_bytes) != os.environ["FACTORY_BUILD_INPUT_DIGEST"]:
    raise SystemExit("Coder received the wrong build-input bytes")
build_input = json.loads(input_bytes)

plan_path = Path(os.environ["FACTORY_BUILD_PLAN_PATH"])
plan_bytes = plan_path.read_bytes()
if _digest(plan_bytes) != os.environ["FACTORY_BUILD_PLAN_SOURCE_DIGEST"]:
    raise SystemExit("Coder received the wrong build-plan bytes")
plan = json.loads(plan_bytes)

catalog_path = Path(os.environ["FACTORY_PATTERN_CATALOG_PATH"])
catalog_bytes = catalog_path.read_bytes()
if _digest(catalog_bytes) != os.environ["FACTORY_PATTERN_CATALOG_SOURCE_DIGEST"]:
    raise SystemExit("Coder received the wrong pattern-catalog bytes")
catalog = json.loads(catalog_bytes)

repair_brief_present = False
repair_brief_path = os.environ.get("FACTORY_REPAIR_BRIEF_PATH")
if repair_brief_path:
    repair_brief_bytes = Path(repair_brief_path).read_bytes()
    if _digest(repair_brief_bytes) != os.environ["FACTORY_REPAIR_BRIEF_ENVELOPE_DIGEST"]:
        raise SystemExit("Coder received changed repair-brief bytes")
    repair_envelope = json.loads(repair_brief_bytes)
    if repair_envelope.get("state", {}).get("kind") != "factory-repair-brief":
        raise SystemExit("Coder received a non-repair Tessera envelope")
    repair_brief_present = True

tester_sentinel = Path.cwd().parent.parent / "tester" / "private" / "sentinel.txt"
try:
    tester_sentinel.read_text(encoding="utf-8")
except OSError:
    cross_lane_read_denied = True
else:
    raise SystemExit("Coder could read the Tester lane")

if build_input.get("schema_version") != "factory-build-input/1":
    raise SystemExit("Coder did not receive compiled phase authority")
patterns = {item["pattern_id"] for item in catalog["patterns"]}
step = plan["steps"][0]
if step["pattern_id"] not in patterns:
    raise SystemExit("build step references an unavailable qualified pattern")
configuration = step["configuration"]
if configuration["operation"] != "integer-addition":
    raise SystemExit("synthetic Coder only implements the authorized operation")
module = configuration["module"]
function = configuration["function"]
implementation = (
    f"def {function}(left: int, right: int) -> int:\n"
    '    """Return the sum required by the signed synthetic specification."""\n'
    + ("    return left - right\n" if "--broken" in sys.argv else "    return left + right\n")
)
output = Path(os.environ["FACTORY_OUTPUT_DIR"])
(output / "artifact").mkdir()
(output / "evidence").mkdir()
(output / "artifact" / module).write_text(implementation, encoding="utf-8")
(output / "evidence" / "lane-evidence.json").write_text(
    json.dumps(
        {
            "role": "coder",
            "build_input_digest": _digest(input_bytes),
            "build_plan_source_digest": _digest(plan_bytes),
            "pattern_catalog_source_digest": _digest(catalog_bytes),
            "cross_lane_read_denied": cross_lane_read_denied,
            "repair_brief_present": repair_brief_present,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
