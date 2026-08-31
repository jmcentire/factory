"""Forcing tests for the keyed core chain (plan 2.2).

The threat this closes: a lane agent who can write ledger.jsonl could re-chain a
whole forged history whose sha256 addresses all re-derive. With HMAC addresses the
forger needs the host-held key — which the negative-space rule keeps out of every
lane environment — so whole-history rewrite fails at ``_verify_records``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from factory_core.manifest import (
    Ledger,
    LedgerEntry,
    LedgerIntegrityError,
    verify_ledger,
)
from factory_runtime.durability import CHAIN_ROOT_KEY_FILENAME, load_chain_key

KEY = b"k" * 32


def _entry(payload: str = "x") -> LedgerEntry:
    return LedgerEntry(
        capability_id="cap-1",
        implementer_identity="impl@x",
        verifier_identity="ver@x",
        approver_identity="appr@x",
        payload={"data": payload},
        created_at="2026-08-31T00:00:00Z",
    )


def _ledger(tmp_path: Path, key: bytes | None) -> Ledger:
    return Ledger(str(tmp_path / "ledger.jsonl"), chain_key=key)


def test_keyed_addresses_use_the_hmac_prefix(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, KEY)
    addr = ledger.append(_entry())
    assert addr.startswith("hmac-sha256:")
    ok, detail = ledger.verify_chain()
    assert ok, detail
    ok, _ = verify_ledger(str(tmp_path / "ledger.jsonl"), chain_key=KEY)
    assert ok


def test_the_prefix_is_the_mode_no_flag_to_lie_about(tmp_path: Path) -> None:
    """A keyed ledger verified unkeyed fails at entry 0, and vice versa — the
    address prefix itself is the mode, so there is no mode flag to forge."""
    keyed = _ledger(tmp_path, KEY)
    keyed.append(_entry())
    ok, detail = _ledger(tmp_path, None).verify_chain()
    assert not ok and "entry 0" in detail

    unkeyed_dir = tmp_path / "unkeyed"
    unkeyed_dir.mkdir()
    with pytest.warns(FutureWarning, match="migration-only"):
        Ledger(str(unkeyed_dir / "ledger.jsonl")).append(_entry())
    ok, detail = Ledger(str(unkeyed_dir / "ledger.jsonl"), chain_key=KEY).verify_chain()
    assert not ok and "entry 0" in detail


def test_wrong_key_fails_and_forged_rechain_without_key_fails(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, KEY)
    ledger.append(_entry("a"))
    ledger.append(_entry("b"))
    ok, _ = _ledger(tmp_path, b"wrong" * 8).verify_chain()
    assert not ok
    # The attack 2.2 closes: rewrite history with self-consistent sha256 addresses.
    # Without the key the forger can only produce sha256-prefixed addresses, which
    # the keyed verifier refuses at entry 0.
    path = tmp_path / "ledger.jsonl"
    forged = Ledger(str(path.parent / "forge" / "ledger.jsonl"))
    with pytest.warns(FutureWarning):
        forged.append(_entry("innocent"))
    path.write_bytes((path.parent / "forge" / "ledger.jsonl").read_bytes())
    ok, detail = ledger.verify_chain()
    assert not ok and "entry 0" in detail
    with pytest.raises(LedgerIntegrityError):
        ledger.append(_entry("c"))


def test_new_unkeyed_ledger_is_loud(tmp_path: Path) -> None:
    with pytest.warns(FutureWarning, match="migration-only"):
        _ledger(tmp_path, None).append(_entry())


def test_existing_unkeyed_ledger_appends_quietly(tmp_path: Path) -> None:
    """Migration posture: extending an EXISTING unkeyed ledger does not warn on every
    append — only new-ledger construction is the loud event."""
    import warnings as _warnings

    ledger = _ledger(tmp_path, None)
    with pytest.warns(FutureWarning):
        ledger.append(_entry("genesis"))
    with _warnings.catch_warnings():
        _warnings.simplefilter("error")
        ledger.append(_entry("second"))


# --------------------------------------------------------------------------- #
# Construction-site enumeration (the mechanical forcing test)
# --------------------------------------------------------------------------- #

_ADMITTED_SITES = {
    # module -> constructions that must thread chain_key explicitly
    "factory_core/manifest.py": 1,  # verify_ledger wrapper
    "factory_runtime/state.py": 1,
    "factory_runtime/resources.py": 1,
    "factory_runtime/evidence_plane.py": 1,
    "factory_runtime/resume.py": 1,
}

_CONSTRUCTION = re.compile(r"(?<![\w.])Ledger\(")


def test_every_ledger_construction_site_threads_the_chain_key() -> None:
    """A new ``Ledger(`` construction site cannot be added unkeyed without turning
    this red: every site in the admitted set must pass ``chain_key=`` explicitly,
    and no site outside the set may construct a core Ledger at all."""
    repo = Path(__file__).resolve().parent.parent
    found: dict[str, int] = {}
    for module_dir in ("factory_core", "factory_runtime"):
        for path in sorted((repo / module_dir).glob("*.py")):
            text = path.read_text(encoding="utf-8")
            count = 0
            for match in _CONSTRUCTION.finditer(text):
                window = text[match.start() : match.start() + 240]
                if "class Ledger" in text[max(0, match.start() - 30) : match.start()]:
                    continue
                count += 1
                assert "chain_key=" in window, (
                    f"{path.relative_to(repo)} constructs Ledger without an explicit "
                    f"chain_key= (plan 2.2: every site threads the key or names None "
                    f"deliberately): ...{window[:120]!r}"
                )
            if count:
                found[str(path.relative_to(repo))] = count
    assert found == _ADMITTED_SITES, (
        f"Ledger construction sites changed: {found} != admitted {_ADMITTED_SITES}. "
        f"A new site must thread chain_key and be admitted here in the same change."
    )


# --------------------------------------------------------------------------- #
# Durability-seam derivation
# --------------------------------------------------------------------------- #

def test_chain_key_derivation_is_root_recoverable_and_per_ledger(tmp_path: Path) -> None:
    (tmp_path / CHAIN_ROOT_KEY_FILENAME).write_bytes(b"root-material\n")
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    a = load_chain_key(run / "ledger.jsonl")
    b = load_chain_key(run / "resources.jsonl")
    again = load_chain_key(run / "ledger.jsonl")
    assert a and b and a != b  # per-ledger identity binding
    assert a == again  # deterministic: recoverable from (root, path) alone
    assert load_chain_key(tmp_path.parent / "elsewhere.jsonl") is None  # no root -> None


def test_run_store_round_trips_keyed_when_root_material_present(tmp_path: Path) -> None:
    """End-to-end threading proof: with root material at the runs root, a real
    RunStore run appends keyed entries and reloads them through verification."""
    import json

    from factory_runtime.state import RunStore
    from tests.conftest import create_intake_run

    (tmp_path / CHAIN_ROOT_KEY_FILENAME).write_bytes(b"root-material\n")
    runs = tmp_path / "runs"
    runs.mkdir()
    store = RunStore(runs)
    from factory_core.manifest import digest_obj

    create_intake_run(
        store,
        run_id="r1",
        target_digest="sha256:" + "a" * 64,
        source_digest=digest_obj({"source": "r1"}),
    )
    first = json.loads(
        (runs / "r1" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert first["entry_hash"].startswith("hmac-sha256:")
    projection = store.load("r1")  # verification happens on load
    assert projection.run_id == "r1"
