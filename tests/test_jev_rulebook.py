"""Structural checks on the jev rules screen (docs/proposals/jev-screen/).

The rulebook is data the resident Orchestrator consumes by rule id, so its shape is load-bearing
even though nothing calls jev in CI. These tests never touch the TypeSafe API: they import only
`rules.py` and `cases_msg_r2.py`, never `jev.py` (which exits without a key).

What must hold: every rule has an atomic question, the fields it reads, criteria with a `what`,
and a three-way band (yes above 0.7, no below 0.3, escalate between); ids are unique; every case
supplies exactly its rule's fields and has a label; each msg-r2 rule has a positive, a negative,
and a lesson-drawn case; and prompts/orchestrate.md names every msg-r2 rule id it consumes.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCREEN = ROOT / "docs" / "proposals" / "jev-screen"
ORCHESTRATE = ROOT / "prompts" / "orchestrate.md"
VALIDATE = ROOT / "prompts" / "validate.md"

_BACKTICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"jev_screen_{name}", SCREEN / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RULES = _load("rules")
CASES = _load("cases_msg_r2").CASES
MSG_R2_IDS = [r["id"] for r in RULES.RULEBOOK if r["source"].startswith("msg-r2")]


def _adherence_section() -> str:
    text = ORCHESTRATE.read_text(encoding="utf-8")
    start = text.index("## Adherence: what are you allowing to happen?")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_rule_ids_are_unique_and_snake_case() -> None:
    ids = [r["id"] for r in RULES.RULEBOOK]
    assert len(ids) == len(set(ids)), "duplicate rule id in RULEBOOK"
    for rid in ids:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", rid), rid
    assert set(RULES.BY_ID) == set(ids)


def test_each_rule_object_is_registered_once() -> None:
    objects = [id(r["rule"]) for r in RULES.RULEBOOK]
    assert len(objects) == len(set(objects)), "one question registered under two ids"


@pytest.mark.parametrize("entry", RULES.RULEBOOK, ids=lambda e: e["id"])
def test_rule_has_question_fields_criteria_and_band(entry: dict) -> None:
    rule = entry["rule"]
    assert rule["type"] in {"noul", "choice"}

    question = rule["instructions"]["question"]
    assert isinstance(question, str) and question.strip().endswith("?")

    fields = entry["fields"]
    assert fields and all(isinstance(f, str) and f for f in fields)
    assert len(fields) == len(set(fields))
    named = set(_BACKTICK.findall(question))
    assert named, f"{entry['id']}: the question names no field"
    assert named <= set(fields), f"{entry['id']}: question reads {named - set(fields)} undeclared"
    for key in ("inspect", "compare"):
        value = rule["instructions"].get(key)
        if value is None:
            continue
        refs = set(_BACKTICK.findall(value if isinstance(value, str) else " ".join(value)))
        assert refs <= set(fields), f"{entry['id']}: {key} reads {refs - set(fields)} undeclared"

    criteria = rule["criteria"]
    if rule["type"] == "noul":
        assert set(criteria) == {"true", "false"}
    else:
        assert len(criteria) >= 2
    for label, criterion in criteria.items():
        assert isinstance(criterion.get("what"), str) and criterion["what"], (entry["id"], label)
        assert set(criterion) <= {"what", "not_for", "examples"}, (entry["id"], label)

    band = entry["band"]
    assert band == {"yes_above": 0.7, "no_below": 0.3}, f"{entry['id']}: band drifted"
    assert entry["source"]


def test_band_has_three_outcomes() -> None:
    assert RULES.band(0.95) == "yes"
    assert RULES.band(0.71) == "yes"
    assert RULES.band(0.7) == "escalate"
    assert RULES.band(0.5) == "escalate"
    assert RULES.band(0.3) == "escalate"
    assert RULES.band(0.29) == "no"
    assert RULES.band(0.0) == "no"


def test_model_is_pinned() -> None:
    assert RULES.MODEL_ID == "jev-1.13.0"


def test_every_msg_r2_rule_has_cases_and_every_case_has_a_rule() -> None:
    assert MSG_R2_IDS, "no msg-r2 rules registered"
    assert set(CASES) == set(MSG_R2_IDS)


@pytest.mark.parametrize("rid", MSG_R2_IDS)
def test_cases_cover_both_labels_and_a_lesson(rid: str) -> None:
    cases = CASES[rid]
    labels = {label for _, _, _, label in cases}
    assert labels == {True, False}, f"{rid}: needs a positive and a negative case"
    assert any(src == "lesson" for _, src, _, _ in cases), f"{rid}: needs a lesson-drawn case"
    fields = set(RULES.BY_ID[rid]["fields"])
    for cid, src, state, label in cases:
        assert src in {"lesson", "synth", "adv"}, (rid, cid)
        assert isinstance(label, bool), (rid, cid)
        assert set(state) == fields, f"{rid}/{cid}: state {set(state)} != fields {fields}"
        assert all(isinstance(v, str) and v.strip() for v in state.values()), (rid, cid)
        if src == "adv":
            assert label is True, f"{rid}/{cid}: an adversarial case must still fire"


def test_case_ids_are_unique() -> None:
    ids = [cid for cases in CASES.values() for cid, _, _, _ in cases]
    assert len(ids) == len(set(ids))


def test_orchestrator_adherence_section_names_every_msg_r2_rule() -> None:
    section = _adherence_section()
    for rid in MSG_R2_IDS:
        assert f"`{rid}`" in section, f"orchestrate.md Adherence does not consume {rid}"
    tabled = re.findall(r"\| `([a-z0-9_]+)` \| §\d \|", section)
    assert sorted(tabled) == sorted(MSG_R2_IDS), "semantic table and rulebook disagree"
    for check in ("D1", "D2", "D3", "D4"):
        assert f"| {check} |" in section, f"deterministic check {check} missing"
    assert "TESTING.md" in section and "T-rules" in section


def test_validator_answers_adherence_challenges() -> None:
    text = VALIDATE.read_text(encoding="utf-8")
    assert "Adherence: what are you allowing to happen?" in text
    assert "before your next dispatch" in text
    assert "recorded reason" in text
