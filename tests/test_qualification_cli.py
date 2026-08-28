"""CLI boundary tests for the qualify command.

The configuration binding is derived from real compile_role_contract and
derive_effective_directive_contract output — the same documents
prepare-lane-dispatch already produces — not a parallel fixture digest, so
these tests exercise the actual binding the runtime would compute.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest import CaptureFixture

from factory_core.manifest import digest_obj
from factory_runtime.cli import main
from factory_runtime.instruction_control import (
    compile_role_contract,
    derive_effective_directive_contract,
)

DOCTRINE = (
    "## Shared foundation\nshared text\n\n"
    "## Directive — Validator\nvalidator text\n\n"
    "## Directive — Coder\ncoder text\n\n"
    "## Directive — Tester\ntester text\n"
).encode("utf-8")

MODEL = "claude-fable-5"
RUNNER = "claude-code-cli"
TOOL_SCHEMA_DIGEST = digest_obj({"tools": ["kindex", "signet", "bash"]})


def _write(tmp_path: Path, name: str, payload: Any) -> str:
    path = tmp_path / name
    if isinstance(payload, (bytes, bytearray)):
        path.write_bytes(payload)
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _real_configuration_files(tmp_path: Path) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    role_contract = compile_role_contract(doctrine_bytes=DOCTRINE, role="validator")
    directive_contract = derive_effective_directive_contract(
        ledger_bytes=b"",
        provisional_bytes=b"",
        run_id="run-qualify-cli",
        generation=1,
        role="validator",
        evaluated_at=1_000,
    )
    return (
        _write(tmp_path, "role_contract.json", role_contract),
        _write(tmp_path, "effective_directives.json", directive_contract),
        role_contract,
        directive_contract,
    )


def _signed_result(
    *,
    result_id: str,
    run_class: str,
    probe_kind: str,
    scenario_id: str,
    prompt_digest: str,
    directive_contract_digest: str,
    passed: bool,
    position: int,
) -> dict[str, Any]:
    configuration = {
        "model": MODEL,
        "runner": RUNNER,
        "prompt_digest": prompt_digest,
        "tool_schema_digest": TOOL_SCHEMA_DIGEST,
        "directive_contract_digest": directive_contract_digest,
    }
    body = {
        "result_id": result_id,
        "role": "validator",
        "run_class": run_class,
        "probe_kind": probe_kind,
        "scenario_id": scenario_id,
        "configuration_digest": digest_obj(configuration),
        "passed": passed,
        "evaluated_position": position,
    }
    return {
        "result_id": result_id,
        "role": "validator",
        "run_class": run_class,
        "probe_kind": probe_kind,
        "scenario_id": scenario_id,
        "configuration": configuration,
        "passed": passed,
        "evaluated_position": position,
        "evidence": {"body": body, "claimed_digest": digest_obj(body)},
    }


def _full_pass_results(prompt_digest: str, directive_contract_digest: str) -> list[dict]:
    classes = (
        "cold",
        "exact-contract",
        "same-session-resume",
        "compaction-boundary",
    )
    results = []
    position = 100
    for run_class in classes:
        for kind in ("probe", "counter-probe"):
            results.append(
                _signed_result(
                    result_id=f"{run_class}-{kind}",
                    run_class=run_class,
                    probe_kind=kind,
                    scenario_id=f"{run_class}-{kind}-scenario",
                    prompt_digest=prompt_digest,
                    directive_contract_digest=directive_contract_digest,
                    passed=True,
                    position=position,
                )
            )
            position += 1
    return results


def _run_qualify(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    *,
    role_contract_path: str,
    effective_directives_path: str,
    results: list[dict],
) -> tuple[int, dict[str, Any]]:
    argv = [
        "qualify",
        "--role",
        "validator",
        "--role-contract",
        role_contract_path,
        "--effective-directives",
        effective_directives_path,
        "--model",
        MODEL,
        "--runner",
        RUNNER,
        "--tool-schema-digest",
        TOOL_SCHEMA_DIGEST,
        "--results",
        _write(tmp_path, "results.json", results),
    ]
    code = main(argv)
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else {})


def test_qualified_over_real_role_and_directive_contracts(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    role_path, directives_path, role_contract, directive_contract = _real_configuration_files(
        tmp_path
    )
    results = _full_pass_results(digest_obj(role_contract), digest_obj(directive_contract))
    code, payload = _run_qualify(
        tmp_path,
        capsys,
        role_contract_path=role_path,
        effective_directives_path=directives_path,
        results=results,
    )
    assert code == 0
    assert payload["status"] == "qualified"
    assert len(payload["classes"]) == 4
    assert all(c["qualified"] for c in payload["classes"])


def test_role_contract_change_invalidates_qualification_via_the_cli(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Binding to the real contract digest means editing doctrine text truly invalidates."""

    role_path, directives_path, role_contract, directive_contract = _real_configuration_files(
        tmp_path
    )
    results = _full_pass_results(digest_obj(role_contract), digest_obj(directive_contract))

    changed_doctrine = DOCTRINE.replace(b"validator text", b"validator text, revised")
    changed_role_contract = compile_role_contract(
        doctrine_bytes=changed_doctrine, role="validator"
    )
    changed_path = _write(tmp_path, "role_contract_v2.json", changed_role_contract)

    code, payload = _run_qualify(
        tmp_path,
        capsys,
        role_contract_path=changed_path,
        effective_directives_path=directives_path,
        results=results,
    )
    assert code == 0
    assert payload["status"] == "not-qualified"
    assert any(r.startswith("result-stale-configuration:") for r in payload["reasons"])


def test_missing_class_via_cli_is_not_qualified(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    role_path, directives_path, role_contract, directive_contract = _real_configuration_files(
        tmp_path
    )
    results = [
        r
        for r in _full_pass_results(digest_obj(role_contract), digest_obj(directive_contract))
        if r["run_class"] != "compaction-boundary"
    ]
    code, payload = _run_qualify(
        tmp_path,
        capsys,
        role_contract_path=role_path,
        effective_directives_path=directives_path,
        results=results,
    )
    assert code == 0
    assert payload["status"] == "not-qualified"
    assert "missing:compaction-boundary:probe" in payload["reasons"]


def test_malformed_results_are_a_refused_control(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    role_path, directives_path, _, _ = _real_configuration_files(tmp_path)
    bad_results = _write(tmp_path, "bad_results.json", {"not": "an array"})
    code = main(
        [
            "qualify",
            "--role",
            "validator",
            "--role-contract",
            role_path,
            "--effective-directives",
            directives_path,
            "--model",
            MODEL,
            "--runner",
            RUNNER,
            "--tool-schema-digest",
            TOOL_SCHEMA_DIGEST,
            "--results",
            bad_results,
        ]
    )
    assert code == 2
    assert "refused" in capsys.readouterr().err
