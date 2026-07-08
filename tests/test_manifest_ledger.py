"""Ledger tests — hash-chain integrity, tamper detection, and fail-closed SoD."""

from __future__ import annotations

import json

import pytest

from factory_core.manifest import (
    Ledger,
    LedgerEntry,
    SegregationError,
    SegregationPolicy,
    digest_obj,
)


def _entry(impl: str, ver: str, appr: str = "", **kw) -> LedgerEntry:
    return LedgerEntry(
        capability_id="cap-1",
        implementer_identity=impl,
        verifier_identity=ver,
        approver_identity=appr,
        payload={"note": kw.pop("note", "work")},
        created_at="2026-07-02T00:00:00Z",
        **kw,
    )


# --------------------------------------------------------------------------- #
# Content addressing
# --------------------------------------------------------------------------- #

def test_content_address_is_deterministic_and_sensitive() -> None:
    a = _entry("impl@x", "ver@x")
    b = _entry("impl@x", "ver@x")
    assert a.content_digest() == b.content_digest(), "same body -> same address"
    c = _entry("impl@x", "ver@x", note="different")
    assert a.content_digest() != c.content_digest(), "any change -> new address"
    assert a.content_digest().startswith("sha256:")


# --------------------------------------------------------------------------- #
# Hash-chain integrity
# --------------------------------------------------------------------------- #

def test_hash_chain_links_and_verifies(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "ledger.jsonl"))
    h0 = ledger.append(_entry("impl@x", "ver@x", "appr@x", from_state="Draft", to_state="Spec"))
    h1 = ledger.append(_entry("impl@x", "ver@x", "appr@x", from_state="Spec", to_state="Approved"))
    h2 = ledger.append(_entry("impl@x", "ver@x", "appr@x", from_state="Approved", to_state="Built"))

    records = ledger.entries()
    assert [r["entry_hash"] for r in records] == [h0, h1, h2]
    assert records[0]["prev_hash"] == ""          # genesis
    assert records[1]["prev_hash"] == h0          # chained
    assert records[2]["prev_hash"] == h1
    assert [r["seq"] for r in records] == [0, 1, 2]

    ok, detail = ledger.verify_chain()
    assert ok, detail


def test_empty_ledger_is_trivially_intact(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "empty.jsonl"))
    assert len(ledger) == 0
    assert ledger.head_hash() == ""
    ok, _ = ledger.verify_chain()
    assert ok


# --------------------------------------------------------------------------- #
# Tamper detection
# --------------------------------------------------------------------------- #

def test_tamper_breaks_the_chain(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(str(path))
    ledger.append(_entry("impl@x", "ver@x", "appr@x"))
    ledger.append(_entry("impl@x", "ver@x", "appr@x", note="second"))
    ledger.append(_entry("impl@x", "ver@x", "appr@x", note="third"))
    assert ledger.verify_chain()[0]

    # Edit the body of the middle entry WITHOUT recomputing its address (a forger's edit).
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["payload"]["note"] = "tampered"          # body changes; entry_hash left stale
    lines[1] = json.dumps(rec, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")

    ok, detail = ledger.verify_chain()
    assert not ok, "tampering must be detected"
    assert "entry 1" in detail


def test_tampering_with_stored_hash_breaks_the_next_link(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(str(path))
    ledger.append(_entry("impl@x", "ver@x", "appr@x"))
    ledger.append(_entry("impl@x", "ver@x", "appr@x", note="second"))

    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["entry_hash"] = digest_obj({"forged": True})   # swap in a coherent-but-wrong address
    lines[0] = json.dumps(rec, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")

    ok, _ = ledger.verify_chain()
    assert not ok, "a re-addressed entry must fail (address no longer matches its body)"


# --------------------------------------------------------------------------- #
# Segregation of duties (fail closed)
# --------------------------------------------------------------------------- #

def test_sod_rejects_implementer_equals_verifier(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    with pytest.raises(SegregationError):
        ledger.append(_entry("same@x", "same@x", "appr@x"))
    assert len(ledger) == 0, "a refused append must not be written (fail closed)"


def test_sod_rejects_implementer_equals_approver(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    with pytest.raises(SegregationError):
        ledger.append(_entry("same@x", "ver@x", "same@x"))


def test_sod_rejects_verifier_equals_approver(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    with pytest.raises(SegregationError):
        ledger.append(_entry("impl@x", "same@x", "same@x"))


def test_sod_accepts_three_distinct_identities(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "l.jsonl"))
    addr = ledger.append(_entry("impl@x", "ver@x", "appr@x"))
    assert addr.startswith("sha256:")
    assert len(ledger) == 1


def test_sod_canonicalizes_aliases_before_distinctness() -> None:
    # jandrew and its email alias are the SAME human; a policy must catch the overlap.
    policy = SegregationPolicy(
        human_ids=frozenset({"jandrew"}),
        human_aliases={"jandrew@example.com": "jandrew", "jandrew": "jandrew"},
    )
    entry = _entry("jandrew", "jandrew@example.com", "someone@example.com")
    violations = entry.validate_sod(policy)
    assert any("implementer == verifier" in v for v in violations)


def test_policy_rejects_non_human_approver() -> None:
    policy = SegregationPolicy(
        human_ids=frozenset({"jandrew"}),
        human_aliases={"jandrew": "jandrew"},
        excluded_service_identities=frozenset({"claude*", "*-bot"}),
    )
    # an agent identity can never approve
    entry = _entry("impl@x", "ver@x", "claude-opus")
    violations = entry.validate_sod(policy)
    assert any("enrolled human" in v for v in violations)
    # an enrolled human approver is accepted
    ok_entry = _entry("impl@x", "ver@x", "jandrew")
    assert ok_entry.validate_sod(policy) == []


def test_bound_implementer_must_match_provenance_author() -> None:
    entry = _entry(
        "impl@x", "ver@x", "appr@x",
        implementer_provenance={"source": "git", "author_identity": "someone-else@x"},
    )
    violations = entry.validate_sod()
    assert any("does not match the bound author" in v for v in violations)
