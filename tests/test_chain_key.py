"""Forcing tests for the keyed core chain (plan 2.2).

The threat this closes: a lane agent who can write ledger.jsonl could re-chain a
whole forged history whose sha256 addresses all re-derive. With HMAC addresses the
forger needs the host-held key — which the negative-space rule keeps out of every
lane environment — so whole-history rewrite fails at ``_verify_records``.
"""

from __future__ import annotations

import ast
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

def _target_call_nodes(
    source: str, *, symbol: str, module: str
) -> list[ast.Call]:
    """Every call to ``module.symbol`` in ``source``, in any binding form.

    Catches the direct import, an aliased import (``import symbol as x``), the
    defining module's own bare reference, and a module-qualified call
    (``module.symbol(`` / ``leaf.symbol(``) — the forms a ``(?<![\\w.])``
    regex missed (round-7 finding #4). Stated residual: a fully dynamic
    ``getattr`` call is a reviewable event outside this static perimeter.
    """

    parent, _, leaf = module.rpartition(".")
    tree = ast.parse(source)
    local_names: set[str] = set()
    module_handles: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == symbol:
            local_names.add(symbol)  # the defining module's bare reference
        elif isinstance(node, ast.ImportFrom):
            if node.module == module:
                for alias in node.names:
                    if alias.name == symbol:
                        local_names.add(alias.asname or symbol)
            elif node.module == parent:
                for alias in node.names:
                    if alias.name == leaf:
                        module_handles.add(alias.asname or leaf)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    module_handles.add(alias.asname or module)

    def _is_module_ref(value: ast.expr) -> bool:
        if isinstance(value, ast.Name):
            return value.id in module_handles
        return ast.unparse(value) in module_handles

    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id in local_names) or (
            isinstance(func, ast.Attribute)
            and func.attr == symbol
            and _is_module_ref(func.value)
        ):
            calls.append(node)
    return calls


def _ledger_construction_sites(source: str) -> list[bool]:
    """Every core-``Ledger`` construction in ``source``, each flagged threads-key.

    AST-based, so the perimeter is airtight against the forms a regex missed
    (round-7 finding #4): a module-qualified call (``manifest.Ledger(``, which
    a ``(?<![\\w.])`` lookbehind excluded), an aliased import
    (``import Ledger as L; L(...)``), and a ``from factory_core import manifest``
    module handle are all caught, and the ``chain_key=`` check reads the actual
    call keywords rather than a fragile character window. The one stated residual
    is a deliberately dynamic construction (``getattr``) or a subclass of the
    core Ledger — either is a reviewable event that would need its own admission.
    """

    return [
        any(kw.arg == "chain_key" for kw in call.keywords)
        for call in _target_call_nodes(
            source, symbol="Ledger", module="factory_core.manifest"
        )
    ]


def test_every_ledger_construction_site_threads_the_chain_key() -> None:
    """A new core-``Ledger`` construction cannot be added unkeyed without turning
    this red: every site in the admitted set must pass ``chain_key=`` explicitly,
    and no site outside the set may construct a core Ledger at all. The perimeter
    is AST-based (round-7 #4: a regex missed qualified/aliased constructions)."""
    repo = Path(__file__).resolve().parent.parent
    found: dict[str, int] = {}
    for module_dir in ("factory_core", "factory_runtime"):
        for path in sorted((repo / module_dir).glob("*.py")):
            sites = _ledger_construction_sites(path.read_text(encoding="utf-8"))
            for threads_key in sites:
                assert threads_key, (
                    f"{path.relative_to(repo)} constructs Ledger without an explicit "
                    f"chain_key= (plan 2.2: every site threads the key or names None "
                    f"deliberately)"
                )
            if sites:
                found[str(path.relative_to(repo))] = len(sites)
    assert found == _ADMITTED_SITES, (
        f"Ledger construction sites changed: {found} != admitted {_ADMITTED_SITES}. "
        f"A new site must thread chain_key and be admitted here in the same change."
    )


def test_the_enumeration_perimeter_catches_aliased_and_qualified_construction() -> None:
    """Round-7 finding #4: the regex perimeter was permeable — a construction site
    evading the ``(?<![\\w.])Ledger\\(`` pattern reddened nothing. These are the
    exact evasions; the AST perimeter must SEE each one and report its key-threading
    truthfully (an unkeyed one as False), not miss it."""
    aliased = _ledger_construction_sites(
        "from factory_core.manifest import Ledger as L\n"
        "x = L('p')\n"  # aliased, unkeyed — the regex never saw 'Ledger('
    )
    assert aliased == [False], aliased

    qualified = _ledger_construction_sites(
        "import factory_core.manifest\n"
        "x = factory_core.manifest.Ledger('p')\n"  # dotted — regex lookbehind excluded it
    )
    assert qualified == [False], qualified

    from_module = _ledger_construction_sites(
        "from factory_core import manifest\n"
        "x = manifest.Ledger('p', chain_key=k)\n"  # module handle, keyed
    )
    assert from_module == [True], from_module

    # and the true negative: a different class named ...Ledger is not the core one.
    assert _ledger_construction_sites(
        "from factory_runtime.resources import ResourceLedger\n"
        "x = ResourceLedger('p')\n"
    ) == []


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


# --------------------------------------------------------------------------- #
# Negative space: no chain-key material in any lane-visible set (plan 2.2)
# --------------------------------------------------------------------------- #

def test_projection_bundle_refuses_chain_key_material(tmp_path: Path) -> None:
    """A projection root containing root-key material is a staging error surfaced
    loudly — never shipped to a lane, never silently sanitized."""
    from factory_runtime.projection_bundle import (
        ProjectionBundleError,
        bundle_runner_projection,
    )

    root = tmp_path / "projection"
    root.mkdir()
    (root / "readme.md").write_text("ok", encoding="utf-8")
    (root / CHAIN_ROOT_KEY_FILENAME).write_bytes(b"root-material\n")
    with pytest.raises(ProjectionBundleError, match="chain-key material"):
        bundle_runner_projection(
            root,
            projection_receipt={"role": "coder", "sha": "x", "tree": "y"},
            run_id="r1",
            generation=1,
            role="coder",
            target_state_digest="sha256:" + "0" * 64,
            resolved_commit="x",
            resolved_tree="y",
        )


def test_named_secret_store_cannot_name_the_root_key_file(tmp_path: Path) -> None:
    """The secret-name grammar structurally refuses the root key filename, so a
    manifest can never smuggle it into a lane environment by name."""
    from factory_runtime.runner import NamedSecretStore, RunnerError

    (tmp_path / CHAIN_ROOT_KEY_FILENAME).write_bytes(b"root-material\n")
    store = NamedSecretStore(tmp_path)
    with pytest.raises(RunnerError, match="invalid named secret"):
        store.resolve([CHAIN_ROOT_KEY_FILENAME])


# --------------------------------------------------------------------------- #
# Keyed-genesis commitment (plan 2.2)
# --------------------------------------------------------------------------- #

def test_genesis_commitment_binds_local_root_material(tmp_path: Path) -> None:
    """The pure halves of the commitment check: material re-derivation and the two
    refusal shapes (absent material, mismatched material) exercised through the same
    digest the workflow gate compares."""
    from factory_core.manifest import digest_bytes
    from factory_runtime.durability import load_chain_root_material

    keyed = tmp_path / "keyed"
    keyed.mkdir()
    (keyed / CHAIN_ROOT_KEY_FILENAME).write_bytes(b"root-material\n")
    runs = keyed / "runs"
    runs.mkdir()
    located = load_chain_root_material(runs / "r1" / "ledger.jsonl")
    assert located is not None
    material, ancestor = located
    assert ancestor == keyed
    commitment = digest_bytes(material)
    assert commitment == digest_bytes(b"root-material")  # stripped, deterministic

    # The walk-up is bounded: a tree with no governing root within the cap is
    # unkeyed (the sibling tree here is shallow enough that only its own ancestry
    # matters once no root file exists on the path from it to the walk ceiling).
    bare = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h" / "runs"
    bare.mkdir(parents=True)
    assert load_chain_root_material(bare / "r1" / "ledger.jsonl") is None


def test_authority_policy_carries_the_commitment_field() -> None:
    from factory_runtime.authority import AuthorityPolicy

    policy = AuthorityPolicy(
        repository_id="repo",
        policy_id="p1",
        root_public_key="a" * 64,
        principals={},
        bootstrap_enabled=False,
        bootstrap_scope=frozenset(),
        genesis_digest="sha256:" + "0" * 64,
        chain_root_commitment="sha256:" + "1" * 64,
    )
    assert policy.chain_root_commitment.startswith("sha256:")


# --------------------------------------------------------------------------- #
# One-machine guard, half (a): coverage/verdict and promotion single-machine
# enumeration (plan 2.2 topology section)
# --------------------------------------------------------------------------- #

def test_decide_promotion_has_exactly_one_admitted_caller() -> None:
    """Every caller of decide_promotion outside the admitted set is a second
    promotion machine — red here before it can exist."""
    repo = Path(__file__).resolve().parent.parent
    admitted = {"factory_runtime/promotion_gate.py": 1}
    found: dict[str, int] = {}
    for module_dir in ("factory_core", "factory_runtime", "scripts", "harness"):
        base = repo / module_dir
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.py")):
            # AST perimeter (round-7 #4 class): a qualified/aliased call to the
            # canonical machine cannot slip past a regex lookbehind.
            calls = _target_call_nodes(
                path.read_text(encoding="utf-8"),
                symbol="decide_promotion",
                module="factory_core.promotion",
            )
            if calls:
                found[str(path.relative_to(repo))] = len(calls)
    assert found == admitted, (
        f"decide_promotion callers changed: {found} != admitted {admitted} — one "
        f"canonical promotion machine, called by everyone (never a second door)"
    )


def test_verdict_ceiling_vocabulary_is_defined_in_one_module() -> None:
    """The full-set and ceiling branches must be ONE function's vocabulary: the
    verdict label literals may appear only in verdict.py — every other module
    imports the constants, so a re-implemented ceiling cannot fork the ranking."""
    repo = Path(__file__).resolve().parent.parent
    literals = ("pass-on-covered-unknown-on-named",)
    offenders: list[str] = []
    for module_dir in ("factory_core", "factory_runtime", "scripts", "harness"):
        base = repo / module_dir
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.py")):
            if path.name == "verdict.py":
                continue
            text = path.read_text(encoding="utf-8")
            for literal in literals:
                if f'"{literal}"' in text or f"'{literal}'" in text:
                    offenders.append(f"{path.relative_to(repo)}:{literal}")
    assert not offenders, (
        f"verdict ceiling vocabulary re-declared outside verdict.py (import the "
        f"constant instead): {offenders}"
    )
