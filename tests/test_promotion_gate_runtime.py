"""Gate L runtime translator tests — the sole-advancement wire (factory_runtime.promotion_gate).

The translator is thin by design: load promotion_inputs.json -> PromotionRequest.from_dict ->
build SegregationPolicy + CriticalityProfile -> decide_promotion -> PromotionDecision.to_dict.
Its unique logic is the file I/O, the fail-closed refusal on missing/malformed inputs, and the
``_policy_from_dict`` builder. decide_promotion itself is exhaustively tested in
``test_promotion_gate.py``; these tests prove the WIRE (load -> parse -> decide -> emit) and
the translator's own controls, not the pure decision a second time.

A promoting fixture is built by serializing the core test helpers' ``_request()`` (which is
already proven to promote) via ``freeze()`` and round-tripping it through ``from_dict`` — so
a wiring bug that dropped a field ``decide_promotion`` needs would turn the promote into a
block here, where it is visible.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from factory_core.manifest import SegregationPolicy
from factory_runtime.promotion_gate import (
    PROMOTION_VERDICT,
    PromotionGateError,
    _policy_from_dict,
    decide,
    render,
)
from tests.conftest import (
    promoting_chain_entries,
    promoting_promotion_inputs,
    write_promoting_chain,
)
from tests.test_promotion_gate import _profile


def _promoting_inputs(run_root: Path) -> dict[str, Any]:
    """A promotion_inputs.json that decide_promotion PROMOTES (allowed=True)."""
    return promoting_promotion_inputs()


def _write_inputs(run_root: Path, body: Any) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "promotion_inputs.json").write_text(
        json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


# The F3 chain-anchor fixtures live in conftest (``promoting_chain_entries`` builds a
# tamper-evident entry whose bare-hex hash re-derives under the seam's ``_load_chain``;
# ``write_promoting_chain`` writes them at the real ``<H>/receipts/chain.jsonl`` layout the
# seam's ``_chain_path`` derives from run_root). The forged/tampered tests below build custom
# chains inline via ``promoting_chain_entries`` and ``_chain_entry``-equivalent bodies.


def _chain_entry(**fields: Any) -> dict[str, Any]:
    """A tamper-evident chain entry (test-local mirror of conftest's, for forged/tampered cases)."""
    body = dict(fields)
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**body, "hash": digest}


def _write_chain(run_root: Path, entries: list[dict[str, Any]]) -> Path:
    chain_path = run_root.parent.parent / "receipts" / "chain.jsonl"
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    chain_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n", encoding="utf-8"
    )
    return chain_path


def _rechained(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-chain a list of entries from genesis, the way the producers do.

    Strip each entry's stale ``hash`` and ``prev_hash`` and re-link it: genesis
    ``prev_hash`` = 64 zeros, each later entry's ``prev_hash`` = the prior entry's
    recomputed content address. The seam's ``_load_chain`` verifies this linkage
    (Opus R2), so a subset of ``promoting_chain_entries`` (e.g. with one entry
    dropped) must be re-chained from genesis or it fail-closes on a broken link
    before the test's intended assertion is reached. Bodies are otherwise
    preserved (a forged ``changed_paths_digest`` stays forged; the re-chain only
    fixes linkage, not the load-bearing attested values).
    """
    out: list[dict[str, Any]] = []
    prev = "0" * 64
    for e in entries:
        body = {k: v for k, v in e.items() if k not in ("hash", "prev_hash")}
        entry = _chain_entry(**body, prev_hash=prev)
        out.append(entry)
        prev = entry["hash"]
    return out


# --------------------------------------------------------------------------
# Fail-closed: the cage refuses rather than advancing on no evidence.
# --------------------------------------------------------------------------


def test_decide_fail_closed_when_inputs_missing(tmp_path: Path) -> None:
    with pytest.raises(PromotionGateError, match="promotion-inputs-missing"):
        decide(tmp_path / "no-such-run")


def test_decide_fail_closed_when_inputs_unreadable(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "promotion_inputs.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(PromotionGateError, match="promotion-inputs-unreadable"):
        decide(run_root)


def test_decide_fail_closed_when_inputs_not_object(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "promotion_inputs.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(PromotionGateError, match="promotion-inputs-malformed"):
        decide(run_root)


def test_decide_fail_closed_when_inputs_malformed_but_json(tmp_path: Path) -> None:
    """Malformed-but-JSON inputs fail-closed, not as an uncaught traceback (Opus F8).

    A ``human_aliases`` that is a list (not an object) makes ``aliases.items()`` raise
    AttributeError inside ``_policy_from_dict``. Before the fix, the CLI's main() caught
    only (OSError, ValueError), so AttributeError escaped as a traceback (exit 1). The gate
    must refuse this as a malformed input (PromotionGateError -> exit 2), not crash.
    """
    run_root = tmp_path / "run"
    _write_inputs(
        run_root,
        {"request": {}, "policy": {"human_aliases": [["a", "b"]]}, "profile": {}},
    )
    with pytest.raises(PromotionGateError, match="promotion-inputs-malformed"):
        decide(run_root)


def test_decide_fail_closed_when_request_field_malformed(tmp_path: Path) -> None:
    """A malformed scalar field deep in from_dict is wrapped (Opus F8), not escaped as a traceback.

    ``monitor_declared_unit_count: "bad"`` makes ``_as_int`` raise ``PromotionError`` (a
    ``ValueError`` subclass) from inside ``PromotionRequest.from_dict``. Before the fix, the
    CLI's main() caught only ``(OSError, ValueError)`` — but the translator's own decide()
    did not wrap from_dict, so the exception escaped decide() as a raw ValueError traceback
    (exit 1, not the refused-control exit 2). The wrapper converts it to a fail-closed
    PromotionGateError so the CLI refuses cleanly.
    """
    run_root = tmp_path / "run"
    _write_inputs(
        run_root,
        {
            "request": {"monitor_declared_unit_count": "not-a-number"},
            "policy": {},
            "profile": {},
        },
    )
    with pytest.raises(PromotionGateError, match="promotion-inputs-malformed"):
        decide(run_root)


# --------------------------------------------------------------------------
# The wire: load -> from_dict -> decide_promotion -> to_dict.
# --------------------------------------------------------------------------


def test_decide_renders_block_verdict_for_empty_request(tmp_path: Path) -> None:
    """An empty request renders a deterministic BLOCK — proves the full wire without needing
    valid envelopes. decide_promotion default-denies: candidate-digest-missing, no surfaces."""
    run_root = tmp_path / "run"
    _write_inputs(run_root, {"request": {}, "policy": {}, "profile": {}})
    decision = decide(run_root)
    assert decision["allowed"] is False
    assert "candidate-digest-missing" in decision["reasons"]


def test_decide_promotes_valid_request_round_trip(tmp_path: Path) -> None:
    """A serialized promoting request, round-tripped through from_dict, still promotes.

    This is the wiring guard: if the translator dropped a field decide_promotion needs (an
    observation, a receipt envelope, the attestation), the promote would silently become a
    block. The baseline (test_promotion_gate.py) proves ``_request()`` promotes; this test
    proves the translator's load+parse preserves that — including the F3 chain-anchor check,
    which grounds each cited envelope in a real receipt chain entry.
    """
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    write_promoting_chain(run_root)
    decision = decide(run_root)
    assert decision["allowed"] is True, decision["reasons"]
    assert decision["disposition"] == "promote"


def test_render_writes_verdict_file_and_returns_decision(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    write_promoting_chain(run_root)
    decision = render(run_root)
    verdict_path = run_root / PROMOTION_VERDICT
    assert verdict_path.exists()
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written["allowed"] is True
    assert written == decision


def test_render_fail_closed_does_not_write_verdict(tmp_path: Path) -> None:
    """A fail-closed refusal writes NO verdict file — a missing verdict cannot be mistaken
    for a block; the close-path (promote.sh) fail-closes on the CLI's non-zero exit."""
    run_root = tmp_path / "run"
    run_root.mkdir()  # no promotion_inputs.json
    with pytest.raises(PromotionGateError):
        render(run_root)
    assert not (run_root / PROMOTION_VERDICT).exists()


# --------------------------------------------------------------------------
# _policy_from_dict — the translator's own builder (SegregationPolicy has no from_dict).
# --------------------------------------------------------------------------


def test_policy_from_dict_lowercases_alias_keys() -> None:
    policy = _policy_from_dict(
        {"human_ids": ["alice", "bob"], "human_aliases": {"Alice": "alice", "BOB": "bob"}}
    )
    assert isinstance(policy, SegregationPolicy)
    # canonical() lowercases the lookup key; the alias map keys must match that.
    assert policy.canonical("Alice") == "alice"
    assert policy.canonical("BOB") == "bob"
    assert policy.canonical("alice") == "alice"


def test_policy_from_dict_strips_alias_values_and_human_ids(tmp_path: Path) -> None:
    """Identity values are stripped, not just keys (Opus F9).

    canonical() strips the lookup key, and an enrollment identity carries the same
    whitespace either side of the alias map: a value "  alice" must resolve to the human_id
    "alice", and a human_id "  bob  " must be stored as "bob". Empty values/keys are dropped
    so they cannot form a vacuous alias.
    """
    policy = _policy_from_dict(
        {
            "human_ids": ["  alice  ", "bob", "  "],
            "human_aliases": {"  Alice  ": "  alice  ", "": "x", "y": ""},
        }
    )
    assert policy.human_ids == frozenset({"alice", "bob"})
    # The alias resolves a whitespace-padded key to the stripped, lowercased identity.
    assert policy.canonical("  Alice  ") == "alice"
    assert policy.human_aliases == {"alice": "alice"}


def test_policy_from_dict_excludes_and_strips_empty(tmp_path: Path) -> None:
    policy = _policy_from_dict(
        {
            "human_ids": ["alice", "", "  "],
            "excluded_service_identities": ["ci-bot", ""],
            "require_signature": True,
            "allowlist_digest": "sha256:abc",
        }
    )
    assert policy.human_ids == frozenset({"alice"})
    assert policy.excluded_service_identities == frozenset({"ci-bot"})
    assert policy.require_signature is True
    assert policy.allowlist_digest == "sha256:abc"
    # an excluded identity never resolves to a human, even before enrollment.
    assert policy.resolve_human("ci-bot") is None


def test_policy_from_dict_empty_defaults() -> None:
    policy = _policy_from_dict({})
    assert policy.human_ids == frozenset()
    assert policy.human_aliases == {}
    assert policy.excluded_service_identities == frozenset()
    assert policy.require_signature is False
    assert policy.allowlist_digest == ""


def test_criticality_profile_from_dict_round_trips_through_translator(tmp_path: Path) -> None:
    """The translator builds the profile via CriticalityProfile.from_dict; verify a profile with
    surfaces round-trips and drives the criticality resolution (a critical surface needs
    approvers; the promoting fixture's standard surface does not)."""
    profile = _profile()
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, {**_promoting_inputs(run_root), "profile": profile.to_dict()})
    write_promoting_chain(run_root)
    decision = decide(run_root)
    assert decision["allowed"] is True
    # The standard-surface promotion requires zero approvers (auto-promote).
    assert decision["required_approvers"] == 0


# --------------------------------------------------------------------------
# F3: the seam's chain-anchor check — fabrication-enforcement.
# A self-consistent FORGERY (a body+digest the agent invented) passes the pure core, because
# the core cannot read the tamper-evident receipt chain. The seam grounds each cited envelope
# in a real chain entry: a receipt id not in the chain, a chain entry that does not re-derive
# (tampered), or an envelope whose body does not match the entry's projection, all fail-closed.
# An absent envelope (no receipt cited) is left to the core's omission-enforcement, not here.
# --------------------------------------------------------------------------


def test_chain_anchor_promotes_when_envelopes_grounded(tmp_path: Path) -> None:
    """An honest envelope projected from a real chain entry is grounded — the promoting
    fixture promotes with its cited receipts anchored in the chain."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    write_promoting_chain(run_root)
    decision = decide(run_root)
    assert decision["allowed"] is True, decision["reasons"]
    assert decision["disposition"] == "promote"


def test_chain_anchor_fail_closed_when_chain_absent(tmp_path: Path) -> None:
    """Present cited envelopes with no receipt chain cannot be grounded — the seam cannot
    verify, so it fail-closes rather than advancing on unanchored evidence. An attacker who
    deletes the chain and forges self-consistent envelopes cannot bypass, because
    present-envelope-with-no-chain fail-closes (the only safe answer when the seam cannot
    verify). The prohibited action under test is the run advancing on unanchored evidence."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    # no chain written -> _load_chain returns {}
    with pytest.raises(PromotionGateError, match="chain-binding|chain-anchor"):
        decide(run_root)


def test_chain_anchor_fail_closed_when_receipt_id_not_in_chain(tmp_path: Path) -> None:
    """A receipt id the agent cites that is not in the real chain is a fabrication (or a
    stale receipt from another run): the envelope may be self-consistent and bind its id, but
    the id is not grounded in this run's receipts. Fail-closed."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    # The chain carries the build and flake entries but NOT the cited oracle receipt
    # M-default (1.1c retargeted this probe from the retired build envelope to the
    # surviving oracle projection — same F3 class). Re-chain the subset from genesis so
    # the seam's linkage check (Opus R2) passes and the test reaches the intended "id
    # not in the chain" assertion, not a broken-link fail-close.
    entries = _rechained([e for e in promoting_chain_entries() if e["id"] != "M-default"])
    _write_chain(run_root, entries)
    with pytest.raises(PromotionGateError, match="M-default.*not in the (verified )?chain"):
        decide(run_root)


def test_chain_anchor_fail_closed_when_envelope_does_not_match_chain(tmp_path: Path) -> None:
    """A self-consistent FORGERY passes the pure core (its body re-derives to its digest and
    its receipt_id binds), but the seam catches it: the envelope attests one value and the
    real chain entry attests another. This is the fabrication route-around F3 closes. The
    envelope stays self-consistent (so the core alone would not catch it); the projection
    mismatch against the real chain entry is what fail-closes."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    # The honest envelope attests oracle_adequate=True; forge the CHAIN entry to attest
    # False (1.1c retargeted this probe from the retired build envelope to the surviving
    # oracle projection). The envelope is still self-consistent (passes the core), but its
    # projection no longer matches the real chain entry. Re-chain from genesis so the forge
    # is in the attested value, not in a broken link (Opus R2) that would fail-close earlier.
    honest = promoting_chain_entries()
    forged_m_body = {k: v for k, v in honest[1].items() if k not in ("hash", "prev_hash")}
    forged_m_body["oracle_adequate"] = False  # != envelope's True
    forged = _rechained([honest[0], forged_m_body, honest[2]])
    _write_chain(run_root, forged)
    with pytest.raises(PromotionGateError, match="forged"):
        decide(run_root)


def test_chain_anchor_fail_closed_when_chain_entry_tampered(tmp_path: Path) -> None:
    """A chain entry whose body was edited after hashing (hash no longer re-derives) is a
    tampered chain — the seam refuses to ground a decision on a chain that is not
    self-consistent. Fail-closed before any envelope is even compared."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    honest = list(promoting_chain_entries())
    # Tamper the oracle entry: flip oracle_adequate after hashing, keep the stale hash.
    tampered = dict(honest[1])
    tampered["oracle_adequate"] = False
    _write_chain(run_root, [honest[0], tampered, honest[2]])
    with pytest.raises(PromotionGateError, match="hash does not re-derive"):
        decide(run_root)


def test_chain_anchor_fail_closed_when_linkage_broken(tmp_path: Path) -> None:
    """Opus R2: per-entry self-consistency is not chain tamper-evidence. An entry
    whose body was edited AND whose hash was recomputed passes the per-entry
    check (its hash re-derives), but if its ``prev_hash`` does not equal the prior
    entry's content address, the chain is broken. The producers write a real
    hash-chain; the seam verifies the linkage, so a per-entry-consistent rewrite
    that breaks the link is caught. The prohibited action is the run advancing
    on a chain that is not tamper-evident as a chain."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    honest = list(promoting_chain_entries())
    # Rewrite the oracle entry in place: flip a body field AND recompute its hash so the
    # per-entry check passes, but leave its prev_hash pointing at the honest prior (so the
    # link to R-default holds) while we corrupt F-default's prev_hash to break the NEXT link.
    tampered_oracle = {k: v for k, v in honest[1].items() if k != "hash"}
    tampered_oracle["verdict_text"] = "rewritten-and-rehashed"
    tampered_oracle = _chain_entry(**{k: v for k, v in tampered_oracle.items() if k != "hash"})
    # Break the link INTO F-default: its prev_hash should be the oracle's content address,
    # but point it at a wrong value. Re-derive F-default's own hash so only the LINK is wrong.
    broken_f = {k: v for k, v in honest[2].items() if k not in ("hash", "prev_hash")}
    broken_f["prev_hash"] = "0" * 63 + "1"  # != oracle's content address
    broken_f = _chain_entry(**broken_f)
    _write_chain(run_root, [honest[0], tampered_oracle, broken_f])
    with pytest.raises(PromotionGateError, match="prev_hash does not link"):
        decide(run_root)


def test_chain_anchor_fail_closed_on_duplicate_id(tmp_path: Path) -> None:
    """Opus R3: the producers append under ``fcntl`` locking, but a second
    correctly-hashed entry reusing a real receipt id is append-only-legal — and under
    last-wins indexing it would shadow the real entry (a green re-use replacing a red
    original, or vice versa). The seam rejects a duplicate id so a re-used id cannot
    substitute one receipt for another. The prohibited action is the run advancing on a
    chain where one receipt id maps to two entries."""
    run_root = tmp_path / "runs" / "run"
    _write_inputs(run_root, _promoting_inputs(run_root))
    honest_green = promoting_chain_entries()
    # The envelope (promotion_inputs) attests F-default GREEN — the run's claim it is
    # deterministic. The REAL chain entry is RED: the run was flaky (deterministic=False,
    # flake_count=2, a failing run in the baseline). With no duplicate, that red honest entry
    # mismatches the green envelope and the seam refuses (forged) — the run does NOT promote on
    # its real receipt. The harm R3 exists to prevent is the FALSE ACCEPTANCE: an attacker
    # appends a GREEN dup (the values the envelope attests) so last-wins indexing shadows the
    # red honest entry with the green forgery, and the run PROMOTES on a receipt it never earned.
    red_f_body = {
        "id": "F-default",
        "kind": "flake",
        "ts": 3,
        "name": "suite",
        "runs": 3,
        "deterministic": False,
        "flake_count": 2,
        "automatic_retry_count": 0,
        "run_exits": [0, 1, 0],
    }
    honest = _rechained([
        {k: v for k, v in honest_green[0].items() if k not in ("hash", "prev_hash")},
        {k: v for k, v in honest_green[1].items() if k not in ("hash", "prev_hash")},
        red_f_body,
    ])
    # The green dup mirrors the original green F-default (the values the envelope attests),
    # linked to the red honest entry so the chain is well-formed.
    green_dup_body = {k: v for k, v in honest_green[2].items() if k not in ("hash", "prev_hash")}
    green_dup_body["ts"] = 4
    green_dup_body["prev_hash"] = honest[2]["hash"]
    green_dup = _chain_entry(**green_dup_body)
    # The dup is genuinely a different attested fact from the honest entry it shadows (green vs
    # red). Under last-wins WITHOUT the dup-id check, F-default would index to the green dup,
    # match the green envelope, and the run would PROMOTE — the false-acceptance the gate
    # prevents (not merely one refusal becoming another). The dup-id check rejects it on id
    # alone, before the envelope is ever compared.
    assert green_dup["deterministic"] != honest[2]["deterministic"]
    assert green_dup["flake_count"] != honest[2]["flake_count"]
    _write_chain(run_root, [*honest, green_dup])
    with pytest.raises(PromotionGateError, match="duplicate receipt id"):
        decide(run_root)


def test_seam_binding_mismatch_and_lane_envelope_ignored(tmp_path: Path) -> None:
    """4.2 change 1's forcing pair, both ends: (a) a self-report contradicting the
    chain-attested value refuses at the seam; (b) a lane-written envelope body in
    the inputs is mechanically IGNORED — it cannot substitute for the chain."""

    from tests.conftest import promoting_promotion_inputs, write_promoting_chain

    run_root = tmp_path / "runs" / "run"
    inputs = promoting_promotion_inputs()
    # (a) contradict the chain: the chain attests oracle_adequate=True; lie low.
    inputs["request"]["observations"][0]["oracle_adequate"] = False
    # (b) plant a forged envelope body claiming the lie is attested — ignored.
    inputs["request"]["observations"][0]["oracle_receipt_evidence"] = {
        "body": {"receipt_id": "M-default", "oracle_adequate": False},
        "claimed_digest": "sha256:" + "0" * 64,
    }
    _write_inputs(run_root, inputs)
    write_promoting_chain(run_root)
    with pytest.raises(PromotionGateError, match="chain-binding.*contradicts"):
        decide(run_root)
