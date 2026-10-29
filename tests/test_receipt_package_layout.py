"""Receipt producers exercise the declared package in the actual copied tree."""

import json
from pathlib import Path

import pytest

from tests.test_harness_scripts import HARNESS, mkpkg, run


@pytest.mark.parametrize("producer", ["mutate", "flake"])
@pytest.mark.parametrize("outside", [False, True])
def test_flat_package_receipts_preserve_import_boundary(
    tmp_path: Path, producer: str, outside: bool,
) -> None:
    tree = mkpkg(tmp_path / "tree")
    (tree / "src" / "pkg").rename(tree / "pkg")
    patch = tmp_path / "patch.py"
    patch.write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1]) / 'pkg/__init__.py'\n"
        "s = p.read_text()\n"
        "assert \"return 'safe'\" in s\n"
        "p.write_text(s.replace(\"return 'safe'\", \"return 'broken'\"))\n"
    )
    args = ["bash", str(HARNESS / f"{producer}.sh"), "layout"]
    if producer == "mutate":
        args += [str(patch), "--named-test", "tests/test_g.py::test_g"]
    else:
        args += ["--runs", "2"]
    args += [
        "--src", str(tree), "--tests", str(tree),
        "--package-root", ".." if outside else ".", "--package", "pkg",
    ]
    result = run(args, tmp_path, {
        "TEST_CMD": "python3 -m pytest tests/ -q -p no:randomly",
        "MUTATE_WORKDIR": str(tmp_path / "work"),
        "HARNESS_DIR": str(tmp_path / "receipts"),
    })
    if outside:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "INVALID-PACKAGE-ROOT" in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        receipt = json.loads(
            (tmp_path / "receipts/receipts/chain.jsonl").read_text().splitlines()[-1]
        )
        if producer == "mutate":
            assert receipt["oracle_adequate"] is True
            assert receipt["outcome"] == "KILLED"
        else:
            assert receipt["run_exits"] == [0, 0]
            assert receipt["deterministic"] is True
