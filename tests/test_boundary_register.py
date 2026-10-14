"""Forcing tests for the boundary register (harness/boundary_register.py).

Each fixture is a defect class that reached production on a consuming target: a role that
was never granted what its service queried, an alert filtering on a resource type no check
produced, a probe that presented no credential, a log field the platform never reads, and a
project-wide grant that reached a service meant to be isolated. Each passed every local
test because each side was internally consistent; only the two sides disagreed.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from harness.boundary_register import (
    REGISTER_NAME,
    SCHEMA,
    BoundaryRegisterError,
    cite,
    contains_token,
    near_miss,
    verify_run,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "harness" / "boundary_register.py"
NOW = datetime.datetime(2026, 10, 14, 15, 27, tzinfo=datetime.UTC)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_record(base: Path, rel: str, line: int, root: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"kind": "file", "path": rel, "line": line, "sha256": sha(base / rel)}
    if root:
        record["root"] = root
    return record


def observation(run: Path, rel: str, command: str) -> dict[str, Any]:
    return {
        "kind": "observation",
        "path": rel,
        "sha256": sha(run / rel),
        "command": command,
        "observed_at": NOW.isoformat(),
    }


@pytest.fixture()
def layout(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    target = tmp_path / "target"
    (run / "artifacts" / "boundary-observations").mkdir(parents=True)
    (target / "gateway").mkdir(parents=True)
    (target / "infra").mkdir(parents=True)
    (target / "gateway" / "db.py").write_text(
        'ROLE = "gateway_ro"\nquery("SELECT revoked FROM user_sessions")\n'
    )
    (target / "infra" / "alerts.tf").write_text('filter = "resource.type=\\"uptime_url\\""\n')
    (target / "infra" / "checks.tf").write_text(
        'monitored_resource { type = "cloud_run_revision" }\n'
    )
    (run / "artifacts" / "boundary-observations" / "grants.txt").write_text(
        "user_sessions | gateway_ro=r/owner\n"
    )
    (run / "artifacts" / "boundary-observations" / "isolated-invokers.json").write_text(
        json.dumps(
            {
                "bindings": [
                    {"role": "roles/run.invoker", "members": ["serviceAccount:ci-deployer@p.iam"]}
                ]
            }
        )
    )
    return run, target


def write_register(run: Path, entries: list[Any], **extra: Any) -> None:
    body = {"schema": SCHEMA, "entries": entries, **extra}
    (run / "artifacts" / REGISTER_NAME).write_text(json.dumps(body, indent=2))


def grant_entry(run: Path, target: Path, *, value: str = "gateway_ro") -> dict[str, Any]:
    return {
        "id": "gateway-db-role",
        "kind": "role",
        "relation": "equal",
        "description": "the gateway's database role must hold SELECT on the tables it reads",
        "sides": [
            {
                "party": "gateway",
                "role": "consumes",
                "value": value,
                "records": [file_record(target, "gateway/db.py", 1)],
            },
            {
                "party": "external:postgres",
                "role": "grants",
                "value": "gateway_ro",
                "records": [
                    observation(
                        run,
                        "artifacts/boundary-observations/grants.txt",
                        "psql -c '\\dp user_sessions'",
                    )
                ],
            },
        ],
    }


def findings(run: Path, target: Path | None, **kwargs: Any) -> list[str]:
    return [f.render() for f in verify_run(run, target=target, **kwargs).findings]


def test_matching_literals_backed_by_records_verify(layout: tuple[Path, Path]) -> None:
    run, target = layout
    write_register(run, [grant_entry(run, target)])
    report = verify_run(run, target=target)
    assert report.ok, report.findings
    assert report.entries == 1 and report.register_digest.startswith("sha256:")


def test_parties_naming_one_thing_differently_is_a_mismatch(layout: tuple[Path, Path]) -> None:
    """The alert listened for a resource type that no check produced."""

    run, target = layout
    entry = {
        "id": "uptime-resource-type",
        "kind": "metric",
        "relation": "equal",
        "description": "the alert filter must select the resource type the checks emit",
        "sides": [
            {
                "party": "alert-policy",
                "role": "consumes",
                "value": "uptime_url",
                "records": [file_record(target, "infra/alerts.tf", 1)],
            },
            {
                "party": "uptime-checks",
                "role": "defines",
                "value": "cloud_run_revision",
                "records": [file_record(target, "infra/checks.tf", 1)],
            },
        ],
    }
    write_register(run, [entry])
    out = findings(run, target)
    assert any(
        "MISMATCH" in item and "uptime_url" in item and "cloud_run_revision" in item for item in out
    ), out


def test_near_miss_is_named_so_the_reader_sees_the_typo() -> None:
    assert near_miss(["svc_auth", "svc-auth"])
    assert near_miss(["UserSessions", "user_sessions"])
    assert not near_miss(["level", "severity"])
    assert not near_miss(["same", "same"])


def test_a_side_with_no_record_is_unverified(layout: tuple[Path, Path]) -> None:
    """A worker presumed to exist with access, with nothing showing it does."""

    run, target = layout
    entry = grant_entry(run, target)
    entry["sides"][1]["records"] = []
    write_register(run, [entry])
    out = findings(run, target)
    assert any("UNVERIFIED" in item and "external:postgres" in item for item in out), out


def test_a_stale_record_must_be_rederived_not_edited(layout: tuple[Path, Path]) -> None:
    run, target = layout
    write_register(run, [grant_entry(run, target)])
    (run / "artifacts" / "boundary-observations" / "grants.txt").write_text(
        "user_sessions | gateway_ro=r/owner\nsessions | gateway_ro=r/owner\n"
    )
    out = findings(run, target)
    assert any("stale" in item and "re-derive" in item for item in out), out


def test_the_cited_line_must_contain_the_literal(layout: tuple[Path, Path]) -> None:
    run, target = layout
    entry = grant_entry(run, target)
    entry["sides"][0]["records"] = [file_record(target, "gateway/db.py", 2)]
    write_register(run, [entry])
    out = findings(run, target)
    assert any("gateway/db.py:2 does not contain 'gateway_ro'" in item for item in out), out


def test_a_value_the_record_never_mentions_is_refused(layout: tuple[Path, Path]) -> None:
    """Both sides typed the same wrong name; the record shows what is actually there."""

    run, target = layout
    entry = grant_entry(run, target, value="gateway_reader")
    entry["sides"][1]["value"] = "gateway_reader"
    write_register(run, [entry])
    out = findings(run, target)
    assert any("does not contain 'gateway_reader'" in item for item in out), out


def test_token_match_does_not_accept_a_longer_name() -> None:
    assert contains_token('"level": "INFO"', "level")
    assert contains_token("serviceAccount:svc-auth@p.iam", "svc-auth")
    assert not contains_token("svc-auth-runtime", "svc-auth")
    assert not contains_token("user_sessions_archive", "user_sessions")
    assert contains_token("GET /health/ready", "/health/ready")


def test_isolation_holds_when_the_subject_is_absent_from_the_policy(
    layout: tuple[Path, Path],
) -> None:
    run, target = layout
    (target / "infra" / "sa.tf").write_text('account_id = "app-run"\n')
    entry = {
        "id": "isolated-service-not-invokable-by-app",
        "kind": "principal",
        "relation": "absent",
        "description": "the isolated service must not be invokable by the shared runtime account",
        "sides": [
            {
                "party": "app-services",
                "role": "presents",
                "value": "app-run",
                "records": [file_record(target, "infra/sa.tf", 1)],
            },
            {
                "party": "external:isolated-service-iam",
                "role": "denies",
                "value": "isolated-service invoker policy",
                "records": [
                    observation(
                        run,
                        "artifacts/boundary-observations/isolated-invokers.json",
                        "gcloud run services get-iam-policy isolated",
                    )
                ],
            },
        ],
    }
    write_register(run, [entry])
    assert findings(run, target) == []


def test_isolation_fails_when_a_wider_grant_reaches_the_subject(
    layout: tuple[Path, Path],
) -> None:
    """The per-service grants said 'cut off'; the policy in force said otherwise."""

    run, target = layout
    (target / "infra" / "sa.tf").write_text('account_id = "app-run"\n')
    policy = run / "artifacts" / "boundary-observations" / "isolated-invokers.json"
    policy.write_text(
        json.dumps(
            {
                "bindings": [
                    {
                        "role": "roles/run.invoker",
                        "members": [
                            "serviceAccount:ci-deployer@p.iam",
                            "serviceAccount:app-run@p.iam",
                        ],
                    }
                ]
            }
        )
    )
    entry = {
        "id": "isolated-service-not-invokable-by-app",
        "kind": "principal",
        "relation": "absent",
        "description": "the isolated service must not be invokable by the shared runtime account",
        "sides": [
            {
                "party": "app-services",
                "role": "presents",
                "value": "app-run",
                "records": [file_record(target, "infra/sa.tf", 1)],
            },
            {
                "party": "external:isolated-service-iam",
                "role": "denies",
                "value": "isolated-service invoker policy",
                "records": [
                    observation(
                        run,
                        "artifacts/boundary-observations/isolated-invokers.json",
                        "gcloud projects get-iam-policy p",
                    )
                ],
            },
        ],
    }
    write_register(run, [entry])
    out = findings(run, target)
    assert any("LEAK" in item and "app-run" in item for item in out), out


@pytest.mark.parametrize("value", ["TODO", "tbd", "<project>-sa", "???", "unknown", "assumed"])
def test_a_placeholder_is_not_a_literal(layout: tuple[Path, Path], value: str) -> None:
    run, target = layout
    entry = grant_entry(run, target)
    entry["sides"][0]["value"] = value
    write_register(run, [entry])
    assert any("placeholder" in item for item in findings(run, target))


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/hosts", "gateway/../../x"])
def test_records_cannot_escape_their_root(layout: tuple[Path, Path], path: str) -> None:
    run, target = layout
    entry = grant_entry(run, target)
    entry["sides"][0]["records"][0]["path"] = path
    write_register(run, [entry])
    assert any("unreadable" in item for item in findings(run, target))


def test_an_observation_must_say_what_produced_it(layout: tuple[Path, Path]) -> None:
    run, target = layout
    entry = grant_entry(run, target)
    del entry["sides"][1]["records"][0]["command"]
    write_register(run, [entry])
    assert any("command" in item for item in findings(run, target))


def test_target_records_need_a_checkout(layout: tuple[Path, Path]) -> None:
    run, target = layout
    write_register(run, [grant_entry(run, target)])
    assert any("no checkout was supplied" in item for item in findings(run, None))


def test_an_empty_register_must_say_why(layout: tuple[Path, Path]) -> None:
    run, target = layout
    write_register(run, [])
    assert any("no_boundary_basis" in item for item in findings(run, target))
    write_register(run, [], no_boundary_basis="pure in-process library; no I/O, no credentials")
    assert findings(run, target) == []


def test_a_current_run_cannot_omit_the_register(layout: tuple[Path, Path]) -> None:
    run, target = layout
    (run / "harness.json").write_text(
        json.dumps({"agreement_contract_version": "factory-agreement-contract/1"})
    )
    with pytest.raises(BoundaryRegisterError, match="must register every boundary"):
        verify_run(run, target=target)


def test_a_legacy_run_without_a_register_is_not_reinterpreted(layout: tuple[Path, Path]) -> None:
    run, target = layout
    report = verify_run(run, target=target)
    assert report.ok and report.notes == ("legacy run without a boundary register",)


def test_candidate_may_edit_a_cited_file_but_not_drop_the_literal(
    layout: tuple[Path, Path], tmp_path: Path
) -> None:
    run, target = layout
    write_register(run, [grant_entry(run, target)])
    candidate = tmp_path / "candidate"
    (candidate / "gateway").mkdir(parents=True)
    (candidate / "gateway" / "db.py").write_text('# reads revocation state\nROLE = "gateway_ro"\n')
    report = verify_run(run, target=target, candidate=candidate)
    assert report.ok, report.findings
    assert any("still present" in note for note in report.notes)

    (candidate / "gateway" / "db.py").write_text('ROLE = "gateway_reader"\n')
    out = findings(run, target, candidate=candidate)
    assert any("no longer in it" in item for item in out), out


def test_cite_builds_the_record_from_the_source(layout: tuple[Path, Path]) -> None:
    run, target = layout
    record = cite(kind="file", base=target, path="gateway/db.py", value="gateway_ro")
    assert record == file_record(target, "gateway/db.py", 1)
    with pytest.raises(ValueError, match="does not contain"):
        cite(kind="file", base=target, path="gateway/db.py", value="gateway_reader")
    seen = cite(
        kind="observation",
        base=run,
        path="artifacts/boundary-observations/grants.txt",
        value="gateway_ro",
        command="psql -c '\\dp user_sessions'",
        now=NOW,
    )
    assert seen["observed_at"] == NOW.isoformat(timespec="seconds")


def test_cli_exit_status_follows_the_verdict(layout: tuple[Path, Path]) -> None:
    run, target = layout
    write_register(run, [grant_entry(run, target)])
    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--root", str(run), "--workdir", str(target)],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    entry = grant_entry(run, target)
    entry["sides"][1]["records"] = []
    write_register(run, [entry])
    bad = subprocess.run(
        [sys.executable, str(SCRIPT), "verify", "--root", str(run), "--workdir", str(target)],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1 and "UNVERIFIED" in bad.stdout
