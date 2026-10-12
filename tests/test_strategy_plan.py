"""The Tester lane is checked against the agreed Testing Strategy (dogfood #36, #65, #73)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from harness import strategy_plan

UPSTREAM = b"route: /site\n# keep this comment exactly\n"
DIGEST = hashlib.sha256(UPSTREAM).hexdigest()


def _strategy(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "testing-strategy.md"
    path.write_text("# Testing strategy\n" + "".join(line + "\n" for line in lines))
    return path


def test_a_cited_oracle_input_must_be_in_the_testers_tree_as_its_pinned_bytes(
    tmp_path: Path,
) -> None:
    strategy = _strategy(tmp_path, f"- oracle-input: tools/gateway/site-route.yaml sha256:{DIGEST}")
    repo = tmp_path / "tester"
    (repo / "tools" / "gateway").mkdir(parents=True)
    check = ["oracle-inputs", "--strategy", str(strategy), "--repo", str(repo)]
    assert strategy_plan.main(check) == 70  # never projected (#36)
    copy = repo / "tools" / "gateway" / "site-route.yaml"
    copy.write_bytes(b"route: /site\n# keep this comment  exactly\n")
    assert strategy_plan.main(check) == 70  # a formatter's copy (#65)
    copy.write_bytes(UPSTREAM)
    assert strategy_plan.main(check) == 0


def test_a_malformed_binding_line_is_refused_not_skipped(tmp_path: Path) -> None:
    strategy = _strategy(tmp_path, "oracle-input: tools/gateway/site-route.yaml")
    assert strategy_plan.main(["oracle-inputs", "--strategy", str(strategy),
                               "--repo", str(tmp_path)]) == 70

