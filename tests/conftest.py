"""Test configuration — make the repo root importable without requiring an install.

Inserting the repo root on ``sys.path`` lets ``import factory_core`` (and importing the
``scripts`` guard) work whether or not the package has been pip-installed, so ``make test``
runs from a bare checkout.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_TARGET = FIXTURES / "synthetic_target" / "target.toml"


def ratification_receipts(phase: str) -> dict[str, str]:
    """The two receipt digests a `*-ratified` transition must name, for a test driving the store.

    `RunStore.transition` refuses a ratification that does not name a human receipt and a distinct
    Validator receipt (`factory_runtime.state._require_ratification_receipts`), so a test that walks
    the states directly has to supply them. Derived from the phase name so every value is distinct
    from every other and from any artifact digest — the store checks distinctness, and a helper that
    handed back one constant would defeat the check it exists to satisfy.

    These are stand-in digests, NOT verified receipts. Only `WorkflowEngine.ratify_phase` verifies a
    receipt; see `tests/test_runtime_workflow.py` and the real-Tessera integration test for that.
    """
    return {
        f"{phase}:{role}-receipt": "sha256:"
        + hashlib.sha256(f"{phase}:{role}-receipt".encode()).hexdigest()
        for role in ("human", "validator")
    }


def generation_artifacts(seed: str = "default") -> dict[str, str]:
    """Complete stand-in generation tuple for state-machine unit tests.

    These values exercise ledger admissibility only. Runtime generation tests create and verify
    real retained target/catalog/plan/input bytes before using the same transition.
    """

    from factory_runtime.state import GENERATION_ARTIFACT_KEYS

    return {
        key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        for key in GENERATION_ARTIFACT_KEYS
    }


def create_intake_run(
    store: Any,
    *,
    run_id: str,
    target_digest: str,
    source_digest: str,
    target_manifest_source_digest: str | None = None,
) -> Any:
    """Drive a RunStore through the v3 Stage-R/target-state/Stage-E intake boundary.

    Store-level tests do not exercise Git or Tessera; this fixture supplies canonical stand-in
    digests and a schema-valid target-state so those tests still begin at intake without adding a
    production bypass around the two-stage authority model.
    """

    def address(label: str) -> str:
        return "sha256:" + hashlib.sha256(f"{run_id}:{label}".encode()).hexdigest()

    manifest_source = target_manifest_source_digest or address("target-manifest-source")
    resource_head = address("resource-ledger")
    run_dir = (store.root / run_id).resolve()
    source_root = run_dir / "target" / "source"
    commit = hashlib.sha256(f"{run_id}:commit".encode()).hexdigest()[:40]
    store.create(
        run_id,
        target_digest=target_digest,
        actor="validator",
        artifact_digests={
            "target-manifest-source": manifest_source,
            "target-resolution-request": address("target-resolution-request"),
            "target-resolution-receipt": address("target-resolution-receipt"),
            "authority-genesis": address("authority-genesis"),
        },
        payload={"authority_receipt_nonces": [f"{run_id}-resolution-nonce"]},
    )
    target_state = {
        "schema_version": "factory-target-state/1",
        "run_id": run_id,
        "repository_id": "fixture",
        "generation": 1,
        "target_id": "fixture",
        "target_manifest_digest": target_digest,
        "target_manifest_source_digest": manifest_source,
        "requested_url": "https://example.test/repository.git",
        "canonical_url": "https://example.test/repository.git",
        "requested_ref": "refs/heads/main",
        "observed_ref_object": commit,
        "peeled_object": commit,
        "resolved_commit": commit,
        "resolved_tree": hashlib.sha256(f"{run_id}:tree".encode()).hexdigest()[:40],
        "control_root": str(run_dir),
        "object_store": str(run_dir / "target" / "objects.git"),
        "source_root": str(source_root),
        "subpath": "",
        "workdir": str(source_root),
        "checkout_id": address("checkout"),
        "observation_method": "remote",
        "remote_freshness": "PROVED",
        "contact_ledger_head": address("contact-ledger"),
        "resource_ledger_head": resource_head,
        "created_at": 1,
    }
    store.record_target_state(
        run_id,
        target_state=target_state,
        actor="target-resolver",
        artifact_digests={"resource-ledger": resource_head},
    )
    return store.authorize_intake(
        run_id,
        source_digest=source_digest,
        actor="validator",
        artifact_digests={
            "execution-request": address("execution-request"),
            "execution-receipt": address("execution-receipt"),
            "authority-genesis": address("authority-genesis"),
        },
        payload={"authority_receipt_nonces": [f"{run_id}-execution-nonce"]},
        approver_identity="human-approver",
    )


def terminalize_run_resources(store: Any, *, run_id: str) -> str:
    """Give a state-machine unit run one explicitly retained run-owned resource.

    The production resolver creates several real resources.  Store-level tests intentionally do
    not invoke Git, but a successful ``PROMOTED`` transition must still exercise the same
    resource-close precondition instead of acquiring a test-only bypass.  The transition itself
    installs the terminal seal; this helper stops at a closeable ledger head.
    """

    from factory_runtime.resources import ResourceLedger

    ledger = ResourceLedger(store.root / run_id, run_id, clock=lambda: 100)
    identifier = str((store.root / run_id / "fixture-retained-resource").resolve())
    common = {
        "generation": 1,
        "resource_id": "fixture-retained-resource",
        "resource_type": "source-worktree",
        "identifier": identifier,
        "creator_action": "state-machine-test-fixture",
        "ownership": "run-owned",
        "baseline": {"absent_at_plan": True},
        "evidence_digests": {},
        "actor": "fixture",
    }
    ledger.append(**common, disposition={}, status="planned")
    ledger.append(**common, disposition={}, status="active")
    ledger.append(
        **common,
        disposition={"reason": "retained state-machine fixture", "residue": True},
        status="retained",
    )
    return ledger.head()


def _freeze(obj: object) -> object:
    """Serialize a dataclass request/policy/profile to the dict shape ``from_dict`` reads.

    Shared by the Gate L translator tests and the promote.sh end-to-end tests so both build the
    same promoting fixture from the proven core helpers. Sets/frozensets -> sorted lists, tuples
    -> lists, Mappings (incl. mappingproxy) -> dicts, dataclasses -> their field dict.
    ``EvidenceIntegrity`` (body + claimed_digest) freezes to exactly its ``to_dict`` wire shape.
    """
    import dataclasses
    from collections.abc import Mapping

    if isinstance(obj, (set, frozenset)):
        return sorted(str(x) for x in obj)
    if isinstance(obj, Mapping):
        return {str(k): _freeze(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_freeze(x) for x in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _freeze(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    return obj


def promoting_promotion_inputs() -> dict[str, object]:
    """A promotion_inputs.json body that ``decide_promotion`` PROMOTES (allowed=True).

    Built by serializing the core test helpers' ``_request()`` (proven to promote in
    ``test_promotion_gate.py``) plus the roster policy and profile. This is the contract a real
    evidence-production pipeline would gather; reusing the proven request means a wiring bug that
    dropped a field turns the promote into a block here, where it is visible.
    """
    from tests.test_promotion_gate import _profile, _request, _roster

    return {
        "request": _freeze(_request()),
        "policy": _freeze(_roster()),
        "profile": _profile().to_dict(),
    }


def _chain_entry(**fields: Any) -> dict[str, Any]:
    """A tamper-evident chain entry: the producer's body plus its bare-hex content address.

    The hash is ``sha256(json.dumps(body, sort_keys=True, separators=(",",":")))`` with no
    ``"sha256:"`` prefix — the same canonical encoding the seam's ``_load_chain`` re-derives, so
    an honest entry re-derives and a tampered one (body changed, hash not) does not.
    """
    body = dict(fields)
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**body, "hash": digest}


def promoting_chain_entries() -> list[dict[str, Any]]:
    """Receipt chain entries that ground the cited envelopes in ``promoting_promotion_inputs``.

    The fixture cites R-default (build), M-default (oracle), F-default (flake); the chain
    entries carry the real producer fields the seam projection reads (build:
    ``disturbed_surface_ids`` + ``changed_paths_digest``; oracle: ``oracle_adequate``; flake:
    ``deterministic`` + ``flake_count`` + ``automatic_retry_count``). The flake producer writes
    ``automatic_retry_count``; the envelope reads ``retry_count`` — the projection renames it, so
    the chain carries the producer name.

    The entries are hash-CHAINED the way the real producers (``receipt.sh``/``mutate.sh``/
    ``flake.sh``) chain them: each entry's ``prev_hash`` is the prior entry's content address,
    genesis ``prev_hash`` = 64 zeros. The seam's ``_load_chain`` verifies this linkage (Opus
    R2), so a fixture that left every ``prev_hash`` empty would fail-closed at load — the chain
    is built sequentially so each entry links to the one before it.
    """
    entries: list[dict[str, Any]] = []
    prev = "0" * 64
    r = _chain_entry(
        id="R-default",
        kind="build",
        ts=1,
        exit=0,
        disturbed_surface_ids=["standard-surface"],
        changed_paths_digest="sha256:abcd",
        prev_hash=prev,
    )
    entries.append(r)
    prev = r["hash"]
    m = _chain_entry(id="M-default", kind="oracle", ts=2, oracle_adequate=True, prev_hash=prev)
    entries.append(m)
    prev = m["hash"]
    f = _chain_entry(
        id="F-default",
        kind="flake",
        ts=3,
        name="suite",
        runs=3,
        deterministic=True,
        flake_count=0,
        automatic_retry_count=0,
        prev_hash=prev,
    )
    entries.append(f)
    return entries


def write_promoting_chain(run_root: Path) -> Path:
    """Write the receipt chain grounding ``promoting_promotion_inputs`` for a run at ``run_root``.

    Mirrors the real harness layout: run_root = ``<H>/runs/<run>``, chain at
    ``<H>/receipts/chain.jsonl`` (the seam's ``_chain_path`` derives this as
    ``run_root.parent.parent / receipts / chain.jsonl``). Returns the chain path.
    """
    chain_path = run_root.parent.parent / "receipts" / "chain.jsonl"
    chain_path.parent.mkdir(parents=True, exist_ok=True)
    entries = promoting_chain_entries()
    chain_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n",
        encoding="utf-8",
    )
    return chain_path
