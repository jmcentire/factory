"""The target's own rule files enter the run record, and the Validator contract enforces them.

Two delivered PRs broke rules their target's review bot enforces (a default invented where the
source gave none; a failed lookup degraded into a silent fallback). The rules were in the pinned
tree; no seat read them, and the lane freeze hides the guidance files from the Coder and Tester.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.repo_rules import DIRNAME, MANIFEST, SCHEMA, RepoRulesError, snapshot

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harness" / "repo_rules.py"

RULE_8 = "---\nid: '08'\ntitle: Missing values stay missing\n---\nNever invent a default.\n"


def target(tmp_path: Path) -> Path:
    work = tmp_path / "target"
    files = {
        "CLAUDE.md": "# agreement\n",
        "src/app.py": "print()\n",
        "services/api/ARCHITECTURE.md": "# arch\n",
        ".rule-review/rules/08-missing.md": RULE_8,
        ".claude/rules/style.md": "no front matter\n",
        "node_modules/pkg/CLAUDE.md": "not the target's\n",
        "docs/README.md": "not a rule\n",
    }
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text, encoding="utf-8")
    return work


def test_snapshot_copies_every_rule_source_with_digest_and_front_matter(tmp_path: Path) -> None:
    work = target(tmp_path)
    artifacts = tmp_path / "artifacts"

    manifest = snapshot(work, artifacts)

    assert manifest["schema"] == SCHEMA
    by_path = {entry["path"]: entry for entry in manifest["files"]}
    assert sorted(by_path) == [
        ".claude/rules/style.md",
        ".rule-review/rules/08-missing.md",
        "CLAUDE.md",
        "services/api/ARCHITECTURE.md",
    ]
    rule = by_path[".rule-review/rules/08-missing.md"]
    assert rule["id"] == "08" and rule["title"] == "Missing values stay missing"
    assert rule["sha256"] == hashlib.sha256(RULE_8.encode()).hexdigest()
    assert "id" not in by_path[".claude/rules/style.md"]
    out = artifacts / DIRNAME
    assert (out / ".rule-review/rules/08-missing.md").read_text(encoding="utf-8") == RULE_8
    assert json.loads((out / MANIFEST).read_text(encoding="utf-8")) == manifest


def test_snapshot_is_frozen_for_the_run(tmp_path: Path) -> None:
    work = target(tmp_path)
    artifacts = tmp_path / "artifacts"
    first = snapshot(work, artifacts)
    assert snapshot(work, artifacts) == first

    (work / ".rule-review/rules/08-missing.md").write_text(RULE_8 + "edited\n", encoding="utf-8")
    with pytest.raises(RepoRulesError, match="frozen"):
        snapshot(work, artifacts)


def test_snapshot_never_follows_a_link_out_of_the_pinned_tree(tmp_path: Path) -> None:
    work = target(tmp_path)
    secret = tmp_path / "outside.md"
    secret.write_text("host file\n", encoding="utf-8")
    (work / "AGENTS.md").symlink_to(secret)

    manifest = snapshot(work, tmp_path / "artifacts")

    agents = next(entry for entry in manifest["files"] if entry["path"] == "AGENTS.md")
    assert "sha256" not in agents and "symlink" in agents
    assert not (tmp_path / "artifacts" / DIRNAME / "AGENTS.md").exists()


def test_cli_lists_each_rule_with_its_id(tmp_path: Path) -> None:
    work = target(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "snapshot", "--workdir", str(work),
         "--artifacts", str(tmp_path / "artifacts")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ".rule-review/rules/08-missing.md\t08" in result.stdout.splitlines()


def test_validator_contract_reads_the_repo_rules_before_phase_a() -> None:
    procedure = (ROOT / "prompts" / "validate.md").read_text(encoding="utf-8")
    section = procedure[procedure.index("## Repo rules"):procedure.index("## Phase A —")]
    flat = " ".join(section.split())
    for source in ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "ARCHITECTURE.md",
                   ".claude/rules/**", ".rule-review/rules/**"):
        assert f"`{source}`" in flat
    assert "harness/repo_rules.py snapshot" in flat
    assert "**Repo rules** section" in flat
    assert "verbatim into both lane briefs" in flat
    assert "never a silent choice" in flat
    assert "PR body states, per touched rule, why the change complies" in flat

    directive = " ".join((ROOT / "docs" / "VALIDATION-DIRECTIVE.md").read_text("utf-8").split())
    assert "artifacts/repo-rules/" in directive
    assert "without a human-ratified exception is negative evidence" in directive

    ignition = (ROOT / "harness" / "factory.sh").read_text(encoding="utf-8")
    assert "VALIDATOR_PROMPT+=\" Before drafting Phase A, snapshot" in ignition
    assert "$D/repo_rules.py snapshot" in ignition
