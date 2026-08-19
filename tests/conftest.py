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
EMPTY_GIT_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SYNTHETIC_CANDIDATE_BYTES = b"fixture candidate\n"


def synthetic_candidate_digest() -> str:
    """Address the one-file candidate used by state-machine review fixtures."""

    from factory_core.manifest import digest_bytes, digest_obj

    return digest_obj(
        {
            "files": [
                {
                    "path": "artifact.py",
                    "mode": 0o644,
                    "digest": digest_bytes(SYNTHETIC_CANDIDATE_BYTES),
                }
            ]
        }
    )


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


def standin_test_change_authorization_artifacts(seed: str = "default") -> dict[str, str]:
    """Stand-in artifact and dual receipts for RunStore admissibility tests."""

    subject = "test-change-authorization"
    return {
        key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        for key in (
            subject,
            f"{subject}:human-receipt",
            f"{subject}:validator-receipt",
        )
    }


def generation_artifacts(
    seed: str = "default",
    *,
    include_acceptance_catalog: bool = True,
) -> dict[str, str]:
    """Complete stand-in generation tuple for state-machine unit tests.

    These values exercise ledger admissibility only. Runtime generation tests create and verify
    real retained target/catalog/plan/input bytes before using the same transition.
    """

    from factory_runtime.state import GENERATION_ARTIFACT_KEYS

    artifacts = {
        key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        for key in GENERATION_ARTIFACT_KEYS
    }
    artifacts["resume-checkpoint"] = (
        "sha256:" + hashlib.sha256(f"{seed}:resume-checkpoint".encode()).hexdigest()
    )
    if include_acceptance_catalog:
        artifacts.update(
            {
                key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
                for key in (
                    "acceptance-obligation-catalog",
                    "acceptance-obligation-catalog:human-receipt",
                    "acceptance-obligation-catalog:validator-receipt",
                )
            }
        )
    return artifacts


def build_payload(
    *,
    attempt_number: int = 1,
    attempt_limit: int = 1,
    seed: str = "default",
    activate_catalog: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    """First-build payload including the two stand-in catalog-ratification nonces."""

    payload: dict[str, Any] = {
        "attempt_id": f"{seed}-attempt-{attempt_number}",
        "attempt_number": attempt_number,
        "attempt_limit": attempt_limit,
        "resume_checkpoint_id": f"{seed}-checkpoint",
        "anchored_run_ledger_head": "sha256:"
        + hashlib.sha256(f"{seed}:anchored-run-ledger".encode()).hexdigest(),
        "anchored_run_ledger_length": 1,
        **extra,
    }
    authority_nonces: list[str] = []
    if activate_catalog:
        authority_nonces.extend(
            [
                f"{seed}-acceptance-catalog-human-nonce",
                f"{seed}-acceptance-catalog-validator-nonce",
            ]
        )
    if extra.get("changed_existing_tests"):
        authority_nonces.extend(
            [
                f"{seed}-test-change-human-nonce",
                f"{seed}-test-change-validator-nonce",
            ]
        )
    if authority_nonces:
        payload["authority_receipt_nonces"] = authority_nonces
    return payload


def acceptance_catalog_artifacts(
    store: Any,
    *,
    run_id: str = "run-1",
    command: tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Retain a real minimal catalog for state-machine tests that later enter preview.

    Workflow and Tessera integration tests prove signatures and provenance.  State tests need
    actual content-addressed bytes because ledger re-derivation now reopens the catalog/report;
    the receipt values remain explicit stand-ins at this lower layer.
    """

    from factory_core.manifest import digest_obj
    from factory_runtime.acceptance_obligations import (
        AcceptanceObligationCatalog,
        validator_execution_digests,
    )

    projection = store.load(run_id)
    selected_command = command or (sys.executable,)
    command_digest, configuration_digest, environment_digest = validator_execution_digests(
        selected_command
    )
    assertion_digest = digest_obj(
        {"test_id": "fixture-acceptance", "expectation": "fixture passes"}
    )
    document = {
        "schema_version": "factory-acceptance-obligation-catalog/1",
        "catalog_id": "fixture-acceptance-catalog",
        "version": "1",
        "run_id": run_id,
        "generation": projection.generation,
        "target_state_digest": projection.target_state_digest,
        "phase_artifact_digests": dict(projection.phase_artifact_digests),
        "human_ratifier": "human-approver",
        "validator_ratifier": "validator",
        "max_review_rounds": 2,
        "triggers": [
            {
                "trigger_id": "validating-to-preview",
                "from_state": "validating",
                "to_state": "preview",
                "command_digest": command_digest,
                "configuration_digest": configuration_digest,
                "environment_digest": environment_digest,
                "obligations": [
                    {
                        "obligation_id": "fixture-acceptance",
                        "criterion": "The exact fixture acceptance test passes.",
                        "verifier_id": "validator-test-execution-v1",
                        "intent_backreferences": [
                            {
                                "artifact_id": "fixture-product-specification",
                                "artifact_digest": projection.phase_artifact_digests[
                                    "product-specification"
                                ],
                                "item_id": "fixture-criterion",
                                "intent_digest": digest_obj(
                                    {"canonical_statement": "fixture passes"}
                                ),
                            }
                        ],
                        "required_evidence_ids": [
                            "candidate",
                            "acceptance-tests",
                            "coder-output-snapshot",
                            "tester-output-snapshot",
                        ],
                        "test_assertions": [
                            {
                                "test_id": "fixture-acceptance",
                                "assertion_digest": assertion_digest,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    catalog = AcceptanceObligationCatalog.from_dict(document)
    directory = (
        store.root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / catalog.content_digest.removeprefix("sha256:")
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "catalog.json").write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "acceptance-obligation-catalog": catalog.content_digest,
        "acceptance-obligation-catalog:human-receipt": "sha256:"
        + hashlib.sha256(f"{catalog.content_digest}:human".encode()).hexdigest(),
        "acceptance-obligation-catalog:validator-receipt": "sha256:"
        + hashlib.sha256(f"{catalog.content_digest}:validator".encode()).hexdigest(),
    }


def validation_artifacts(
    seed: str = "default",
    *,
    candidate: str | None = None,
) -> dict[str, str]:
    """Exact immutable author outputs required when a v4 run enters validation."""

    values = {
        key: "sha256:" + hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()
        for key in (
            "candidate",
            "acceptance-tests",
            "coder-output-snapshot",
            "tester-output-snapshot",
        )
    }
    if candidate is not None:
        values["candidate"] = candidate
    return values


def preview_artifacts(
    store: Any,
    seed: str = "default",
    *,
    run_id: str = "run-1",
    candidate: str | None = None,
    reviewer_identity: str = "validator",
) -> dict[str, str]:
    """Retain freshly re-derived acceptance and adversarial-review evidence for preview."""

    from factory_core.manifest import digest_bytes, digest_obj
    from factory_runtime.acceptance_obligations import (
        AcceptanceObligationCatalog,
        derive_acceptance_obligation_report,
        retain_acceptance_obligation_report,
    )
    from factory_runtime.adversarial_review import (
        REQUIRED_COMPLETENESS_CHECKS,
        REQUIRED_REVIEW_DIMENSIONS,
        VerifiedAdversarialReview,
        build_review_authority_context,
        build_validator_review_subject,
        retain_validator_adversarial_review,
    )

    projection = store.load(run_id)
    catalog_path = (
        store.root
        / run_id
        / "evidence"
        / "acceptance-obligation-catalogs"
        / projection.acceptance_obligation_catalog_digest.removeprefix("sha256:")
        / "catalog.json"
    )
    catalog = AcceptanceObligationCatalog.from_dict(json.loads(catalog_path.read_text()))
    trigger = catalog.select("validating", "preview")
    obligation = trigger["obligations"][0]
    tests = list(obligation["test_assertions"])
    trusted = {
        key: str(store.current_artifact_digests(run_id)[key])
        for key in (
            "candidate",
            "acceptance-tests",
            "coder-output-snapshot",
            "tester-output-snapshot",
        )
    }
    test_results = [
        {
            **test,
            "exit_status": 0,
            "output_digest": digest_obj(
                {
                    **test,
                    "exit_status": 0,
                    "candidate_digest": trusted["candidate"],
                    "acceptance_tests_digest": trusted["acceptance-tests"],
                    "command_digest": trigger["command_digest"],
                }
            ),
        }
        for test in tests
    ]
    effect_body = {
        "obligation_id": obligation["obligation_id"],
        "verifier_id": obligation["verifier_id"],
        "candidate_digest": trusted["candidate"],
        "acceptance_tests_digest": trusted["acceptance-tests"],
        "command_digest": trigger["command_digest"],
        "configuration_digest": trigger["configuration_digest"],
        "environment_digest": trigger["environment_digest"],
        "started_at": 100,
        "finished_at": 101,
        "evidence_digests": trusted,
        "test_results": test_results,
    }
    observations = {
        "schema_version": "factory-acceptance-obligation-observations/1",
        "run_id": run_id,
        "generation": projection.generation,
        "catalog_digest": catalog.content_digest,
        "trigger_id": trigger["trigger_id"],
        "candidate_digest": trusted["candidate"],
        "acceptance_tests_digest": trusted["acceptance-tests"],
        "command_digest": trigger["command_digest"],
        "configuration_digest": trigger["configuration_digest"],
        "environment_digest": trigger["environment_digest"],
        "started_at": 100,
        "finished_at": 101,
        "results": [
            {
                "obligation_id": obligation["obligation_id"],
                "verifier_id": obligation["verifier_id"],
                "passed": True,
                "evidence_digests": trusted,
                "test_results": test_results,
                "effect_digest": digest_obj(effect_body),
            }
        ],
    }
    report = derive_acceptance_obligation_report(
        catalog,
        observations=observations,
        run_id=run_id,
        generation=projection.generation,
        source="validating",
        destination="preview",
        target_state_digest=projection.target_state_digest,
        resolved_commit=str(projection.target_state["resolved_commit"]),
        resolved_tree=str(projection.target_state["resolved_tree"]),
        phase_artifact_digests=projection.phase_artifact_digests,
        candidate_digest=trusted["candidate"],
        acceptance_tests_digest=trusted["acceptance-tests"],
        command_digest=trigger["command_digest"],
        configuration_digest=trigger["configuration_digest"],
        environment_digest=trigger["environment_digest"],
        trusted_evidence_digests=trusted,
    )
    report_digest = retain_acceptance_obligation_report(store.root, run_id, report)
    catalog_source_digest = digest_bytes(catalog_path.read_bytes())
    snapshot_body = {
        "schema_version": "factory-base-source-snapshot/1",
        "resolved_commit": str(projection.target_state["resolved_commit"]),
        "resolved_tree": str(projection.target_state["resolved_tree"]),
        "hash_algorithm": "sha1",
        "subpath": "",
        "files": [],
    }
    base_source_snapshot = {
        **snapshot_body,
        "snapshot_digest": digest_obj(snapshot_body),
    }
    change_body = {
        "schema_version": "factory-candidate-change-set/1",
        "resolved_commit": str(projection.target_state["resolved_commit"]),
        "resolved_tree": str(projection.target_state["resolved_tree"]),
        "subpath": "",
        "construction_mode": "regenerate",
        "baseline_snapshot_digest": base_source_snapshot["snapshot_digest"],
        "candidate_digest": trusted["candidate"],
        "changed_path_digest": digest_obj(["artifact.py"]),
        "changes": [
            {
                "path": "artifact.py",
                "kind": "added",
                "old_type": None,
                "new_type": "file",
                "old_mode": None,
                "new_mode": 0o644,
                "old_digest": None,
                "new_digest": digest_bytes(SYNTHETIC_CANDIDATE_BYTES),
            }
        ],
    }
    candidate_change_set = {
        **change_body,
        "change_set_digest": digest_obj(change_body),
    }
    checkpoint = {"run_id": run_id, "seed": seed}
    checkpoint_bytes = (
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    configuration_bytes = b'{"fixture":true}\n'
    authority_context = build_review_authority_context(
        resume_checkpoint_digest=digest_obj(checkpoint),
        resume_checkpoint_source_digest=digest_bytes(checkpoint_bytes),
        resume_checkpoint_bytes=checkpoint_bytes,
        configuration_sources={"state-fixture": configuration_bytes},
        expected_configuration_digests={
            "state-fixture": digest_bytes(configuration_bytes)
        },
        changed_existing_tests=(),
        test_change_artifacts={},
        test_change_sources={},
    )
    subject = build_validator_review_subject(
        run_id=run_id,
        generation=projection.generation,
        target_digest=projection.target_digest,
        target_state_digest=projection.target_state_digest,
        resolved_commit=str(projection.target_state["resolved_commit"]),
        resolved_tree=str(projection.target_state["resolved_tree"]),
        reviewer_identity=reviewer_identity,
        base_source_snapshot=base_source_snapshot,
        candidate_change_set=candidate_change_set,
        authority_context=authority_context,
        build_input_digest=str(projection.generation_artifact_digests["build-input"]),
        pattern_catalog_digest=str(
            projection.generation_artifact_digests["pattern-catalog"]
        ),
        pattern_catalog_source_digest=str(
            projection.generation_artifact_digests["pattern-catalog-source"]
        ),
        build_plan_digest=str(projection.generation_artifact_digests["build-plan"]),
        build_plan_source_digest=str(
            projection.generation_artifact_digests["build-plan-source"]
        ),
        phase_artifact_digests=projection.phase_artifact_digests,
        acceptance_obligation_catalog_digest=catalog.content_digest,
        acceptance_obligation_catalog_source_digest=catalog_source_digest,
        candidate_digest=trusted["candidate"],
        acceptance_tests_digest=trusted["acceptance-tests"],
        coder_output_snapshot_digest=trusted["coder-output-snapshot"],
        tester_output_snapshot_digest=trusted["tester-output-snapshot"],
        command_digest=str(report["command_digest"]),
        configuration_digest=str(report["configuration_digest"]),
        environment_digest=str(report["environment_digest"]),
    )

    def review_reference(source: str, path: str) -> dict[str, object]:
        return {
            "source": source,
            "path": path,
            "start_line": 1,
            "end_line": 1,
            "excerpt_digest": digest_obj(
                {"seed": seed, "review_source": source, "path": path}
            ),
        }

    references = [
        review_reference("implementation", "artifact.py"),
        review_reference("acceptance-tests", "acceptance_test.py"),
        review_reference("build-input", "build-input.json"),
        review_reference("pattern-catalog", "pattern-catalog.json"),
        review_reference("build-plan", "build-plan.json"),
        review_reference(
            "acceptance-obligation-catalog", "acceptance-obligation-catalog.json"
        ),
        review_reference(
            "acceptance-observations", "acceptance-obligation-observations.json"
        ),
        review_reference("baseline-source", "fixture-missing-baseline.txt"),
        review_reference("candidate-change-set", "candidate-change-set.json"),
        review_reference(
            "review-authority-context", "review-authority-context.json"
        ),
    ]
    review_report = {
        "schema_version": "factory-validator-adversarial-review/1",
        "authority": "review-evidence-only",
        "subject_digest": digest_obj(subject),
        "reviewer_identity": reviewer_identity,
        "acceptance_observations_digest": str(report["observations_digest"]),
        "dimensions": [
            {
                "dimension_id": dimension,
                "state": "COMPLETED",
                "summary": f"Fixture completed {dimension}.",
                "evidence": [references[0]],
            }
            for dimension in REQUIRED_REVIEW_DIMENSIONS
        ],
        "findings": [],
        "completeness": {
            "state": "COMPLETED",
            "summary": "Fixture completed the independent clean-claim challenge.",
            "checks": [
                {
                    "check_id": check_id,
                    "state": "COMPLETED",
                    "summary": f"Fixture completed {check_id}.",
                    "evidence": [references[2]],
                }
                for check_id in REQUIRED_COMPLETENESS_CHECKS
            ],
            "evidence": references,
        },
        "verdict": "CLEAN_QUALIFIED",
    }
    verified_review = VerifiedAdversarialReview(
        subject=subject,
        report=review_report,
        subject_digest=digest_obj(subject),
        report_digest=digest_obj(review_report),
    )
    review_artifacts = retain_validator_adversarial_review(
        store.root,
        run_id,
        verified_review,
    )
    return {
        "candidate": trusted["candidate"],
        "acceptance-tests": trusted["acceptance-tests"],
        "acceptance-obligation-report": report_digest,
        **dict(review_artifacts),
        "evidence-bundle": "sha256:"
        + hashlib.sha256(f"{seed}:evidence-bundle".encode()).hexdigest(),
        "evidence-envelope": "sha256:"
        + hashlib.sha256(f"{seed}:evidence-envelope".encode()).hexdigest(),
    }


def ci_artifacts(seed: str = "default") -> dict[str, str]:
    """Content-addressed CI result required after human candidate approval."""

    return {"ci-evidence": "sha256:" + hashlib.sha256(f"{seed}:ci-evidence".encode()).hexdigest()}


def create_intake_run(
    store: Any,
    *,
    run_id: str,
    target_digest: str,
    source_digest: str,
    target_manifest_source_digest: str | None = None,
) -> Any:
    """Drive a RunStore through the v4 Stage-R/target-state/Stage-E intake boundary.

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
        "resolved_tree": EMPTY_GIT_TREE_SHA1,
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
