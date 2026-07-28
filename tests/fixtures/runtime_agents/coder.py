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
    raise SystemExit("Coder received the wrong spec bytes")
spec = json.loads(spec_bytes)

tester_sentinel = Path.cwd().parent.parent / "tester" / "private" / "sentinel.txt"
try:
    tester_sentinel.read_text(encoding="utf-8")
except OSError:
    cross_lane_read_denied = True
else:
    raise SystemExit("Coder could read the Tester lane")

interface = spec["interface"]
if interface["operation"] != "integer-addition":
    raise SystemExit("synthetic Coder only implements the authorized operation")
module = interface["module"]
function = interface["function"]
implementation = (
    f"def {function}(left: int, right: int) -> int:\n"
    '    """Return the sum required by the signed synthetic specification."""\n'
    "    return left + right\n"
)
output = Path(os.environ["FACTORY_OUTPUT_DIR"])
(output / "artifact").mkdir()
(output / "evidence").mkdir()
(output / "artifact" / module).write_text(implementation, encoding="utf-8")
(output / "evidence" / "lane-evidence.json").write_text(
    json.dumps(
        {
            "role": "coder",
            "spec_digest": _digest(spec_bytes),
            "cross_lane_read_denied": cross_lane_read_denied,
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)
