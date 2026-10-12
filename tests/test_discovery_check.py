"""The integrated tree's runner must list every test module the Tester froze (dogfood #42)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import discovery_check


def _frozen_tester(tmp_path: Path) -> Path:
    files = tmp_path / "snapshot" / "files"
    (files / "tests").mkdir(parents=True)
    (files / "tests" / "export_test.ts").write_text("// R3.1\n")
    (files / "contracts").mkdir()
    (files / "contracts" / "api.yaml").write_text("openapi: 3.1.0\n")
    root = tmp_path / "run"
    (root / "tmux-lanes").mkdir(parents=True)
    rows = [{"status": "active", "role": "tester"},
            {"schema_version": discovery_check.FREEZE_SCHEMA,
             "export": {"files_directory": str(files)}}]
    (root / "tmux-lanes" / "tester-launch.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows))
    return root


def _check(root: Path, tree: Path, listed: str, pattern: str = "tests/*_test.ts") -> int:
    return discovery_check.main(["--root", str(root), "--tree", str(tree), "--pattern", pattern,
                                 "--", "sh", "-c", f"printf '%s\\n' {listed}"])


def test_a_tree_whose_runner_misses_the_testers_modules_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, tree = _frozen_tester(tmp_path), tmp_path / "integrated"
    tree.mkdir()
    assert _check(root, tree, "'No test modules found'") == 70
    assert "tests/export_test.ts" in capsys.readouterr().err
    assert _check(root, tree, "'Check file:///x/tests/export_test.ts'") == 0
    assert json.loads(capsys.readouterr().out)["modules"] == ["tests/export_test.ts"]


def test_the_check_cannot_pass_vacuously_or_before_freeze(tmp_path: Path) -> None:
    root, tree = _frozen_tester(tmp_path), tmp_path / "integrated"
    tree.mkdir()
    assert _check(root, tree, "anything", pattern="spec/*_test.ts") == 70
    journal = root / "tmux-lanes" / "tester-launch.jsonl"
    journal.write_text(journal.read_text() + json.dumps(
        {"schema_version": "factory-tmux-lane-unfreeze/1"}) + "\n")
    assert _check(root, tree, "'tests/export_test.ts'") == 70
