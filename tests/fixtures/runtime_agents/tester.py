from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


spec_path = Path(os.environ["FACTORY_SPEC_PATH"])
spec_bytes = spec_path.read_bytes()
if _digest(spec_bytes) != os.environ["FACTORY_SPEC_DIGEST"]:
    raise SystemExit("Tester received the wrong spec bytes")
spec = json.loads(spec_bytes)

coder_sentinel = Path.cwd().parent.parent / "coder" / "private" / "sentinel.txt"
try:
    coder_sentinel.read_text(encoding="utf-8")
except OSError:
    cross_lane_read_denied = True
else:
    raise SystemExit("Tester could read the Coder lane")

interface = spec["interface"]
module_name = Path(interface["module"]).stem
function = interface["function"]
examples = spec["acceptance"]
test_source = [
    "from __future__ import annotations",
    "",
    f"from {module_name} import {function}",
    "",
]
for item in examples:
    test_source.extend(
        (
            f"# authority: {item['backreference']['artifact_digest']} "
            f"{item['backreference']['item_id']}",
            (
                f"assert {function}({item['left']!r}, {item['right']!r}) "
                f"== {item['expected']!r}"
            ),
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
                    "claim_id": item["criterion_id"],
                    "kind": "test-assertion",
                    "backreference": item["backreference"],
                }
                for item in examples
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
            "spec_digest": _digest(spec_bytes),
            "cross_lane_read_denied": cross_lane_read_denied,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
