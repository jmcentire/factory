"""Owner delegation grants only the reviewed subjects; it is not blanket agent authority."""

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from factory_core.criticality import CriticalityProfile, resolve_criticality
from factory_core.evidence import EvidenceIntegrity
from factory_core.manifest import digest_obj
from factory_core.promotion import PromotionRequest
from factory_core.tool_policy import authorize_tool_invocation, verify_tool_policy
from factory_runtime.authority import AuthorityVerificationError
from factory_runtime.project_init import init_project, record_human_statement
from factory_runtime.promotion_delegation import apply_delegation, delegation_subject
from factory_runtime.promotion_gate import _policy_from_dict
from tests.test_criticality import _surface
from tests.test_project_init import _cli
from tests.test_single_operator_anchor import _solo_bundle, _solo_roster


def _bundle():
    bundle = _solo_bundle()
    policy = replace(bundle.policy, signed_by="agent:validator")
    probes = []
    for probe in bundle.denial_probes:
        probe = replace(probe, policy_digest=policy.content_digest)
        probes.append(replace(probe, evidence=EvidenceIntegrity(
            body=probe.authority_body(), claimed_digest=digest_obj(probe.authority_body())
        )))
    body = {"policy_digest": policy.content_digest}
    return replace(
        bundle, policy=policy, trusted_policy_digest=policy.content_digest,
        denial_probes=tuple(probes),
        single_operator_anchor=EvidenceIntegrity(body=body, claimed_digest=digest_obj(body)),
    )


def test_delegation_preserves_tool_denials_scopes_and_subject_binding():
    bundle = _bundle()
    policy = replace(_solo_roster(), delegated_policy_signers=frozenset({(
        bundle.policy.run_id, bundle.policy.content_digest, "agent:validator", 1, 150
    )}))
    assert verify_tool_policy(bundle, policy, 100).satisfied
    for tool, scope, allowed in (
        ("workspace", "read-write", True),
        ("workspace", "outside", False),
        ("production-mutation", "write", False),
        ("unknown", "read-write", False),
    ):
        result = authorize_tool_invocation(
            bundle, policy, principal="agent:validator", tool_id=tool,
            scope_id=scope, invocation_id="one", attempted_at=100,
        )
        assert result.allowed is allowed
    for changed in (replace(bundle.policy, run_id="other"),
                    replace(bundle.policy, version="changed"),
                    replace(bundle.policy, signed_by="agent:coder")):
        assert "tool-policy-signer-not-enrolled" in verify_tool_policy(
            replace(bundle, policy=changed), policy, 100
        ).issues
    assert not verify_tool_policy(bundle, policy, 2000).satisfied
    assert "tool-policy-signer-not-enrolled" in verify_tool_policy(bundle, policy, 151).issues
    assert _solo_roster().resolve_human("agent:validator") is None


def test_classification_delegation_preserves_unknown_and_side_effect_fallback():
    profile = CriticalityProfile(surfaces=(
        _surface("display", "cosmetic", decided_by="agent:validator", side_effects=("records",)),
        _surface("records", "critical", decided_by="agent:validator"),
    ))
    policy = replace(_solo_roster(), delegated_classification_deciders=frozenset({
        (profile.content_digest, "agent:validator", 1, 150)
    }))
    resolved = resolve_criticality(profile, ("display",), policy, 100)
    assert resolved.surfaces[0].effective_criticality == "cosmetic"
    assert resolved.highest_criticality == "critical"
    expired = resolve_criticality(profile, ("display",), policy, 151)
    assert expired.surfaces[0].effective_criticality == "critical"
    assert resolve_criticality(profile, ("unknown",), policy).highest_criticality == "critical"
    changed = replace(profile, surfaces=(replace(profile.surfaces[0], wrong_cost="changed"),))
    assert resolve_criticality(changed, ("display",), policy).highest_criticality == "critical"


def test_promotion_inputs_cannot_self_grant_delegated_roles():
    policy = _policy_from_dict({
        "delegated_policy_signers": [["run-1", "digest", "agent:validator"]],
        "delegated_classification_deciders": [["digest", "agent:validator"]],
    })
    assert not policy.delegated_policy_signers
    assert not policy.delegated_classification_deciders


@pytest.mark.tessera_integration
@pytest.mark.parametrize("change", ["none", "profile", "policy", "run", "actor", "expired"])
def test_real_owner_receipt_binds_delegation(tmp_path: Path, change: str):
    tessera = _cli()
    project = init_project(tmp_path / "keys", repository_id="example", tessera=tessera)
    run = tmp_path / "run-1"
    run.mkdir()
    bundle = _bundle()
    profile = CriticalityProfile(surfaces=(
        _surface("display", "cosmetic", decided_by="agent:validator"),
    ))
    request = PromotionRequest.from_dict({
        "verifier": "agent:validator", "tool_policy": bundle.to_dict(),
    })
    subject = delegation_subject(
        run.name, "agent:validator", bundle.policy.content_digest, profile.content_digest
    )
    record_human_statement(
        project, run_id=run.name, action="activate-policy", subject_digest=digest_obj(subject),
        said="Delegate these two reviewed subjects to this run's Validator.", said_at="fixture",
        output_path=run / "delegation.json", tessera=tessera,
        **({"clock": lambda: 1} if change == "expired" else {}),
    )
    raw = {"identity": "agent:validator", "receipt": "delegation.json"}
    if change == "profile":
        profile = replace(profile, surfaces=(replace(profile.surfaces[0], wrong_cost="changed"),))
    elif change == "policy":
        request = replace(
            request, tool_policy=replace(bundle, policy=replace(bundle.policy, version="2"))
        )
    elif change == "run":
        request = replace(
            request, tool_policy=replace(bundle, policy=replace(bundle.policy, run_id="other"))
        )
    elif change == "actor":
        raw["identity"] = "agent:coder"
    args = (run, raw, request, profile, project.policy.segregation_policy())
    if change == "none":
        result = apply_delegation(*args, authority=project.policy, tessera=tessera)
        assert any(row[:3] == (run.name, bundle.policy.content_digest, "agent:validator")
                   for row in result.delegated_policy_signers)
        assert result.resolve_human("agent:validator") is None
        (run / "promotion_inputs.json").write_text(json.dumps({
            "request": {"verifier": "agent:validator", "tool_policy": bundle.to_dict(),
                        "evaluated_at": int(time.time())},
            "profile": profile.to_dict(),
            "policy": {"human_ids": ["human:owner"]}, "delegation": raw,
        }))
        env = os.environ.copy()
        env.update(project.worker_env("validator", tessera_bin=tessera.command[0]))
        command = (
            "from pathlib import Path; import json,sys; "
            "from factory_runtime.promotion_gate import decide; "
            "print(json.dumps(decide(Path(sys.argv[1]))))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", command, str(run)], env=env,
            text=True, capture_output=True, check=True,
        )
        decision = json.loads(completed.stdout)
        assert "tool-policy-invalid:tool-policy-signer-not-enrolled" not in decision["reasons"]
    else:
        with pytest.raises(AuthorityVerificationError):
            apply_delegation(*args, authority=project.policy, tessera=tessera)
