"""Forcing tests for the 4.1 trust root.

Two rails: the enrollment roster must be operator-owned (outside every
agent-writable surface), and enrolled-human keys are minted/used only outside
the host process — a host signing seam handed a human's key refuses to mint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory_runtime.authority import (
    AuthorityPolicy,
    AuthorityVerificationError,
    Principal,
    human_public_keys,
    require_operator_owned_trust_root,
)


def _policy(**overrides) -> AuthorityPolicy:
    principals = {
        "human:founder": Principal(
            identity="human:founder",
            kind="human",
            public_key="a" * 64,
            capabilities=frozenset({"ratify"}),
        ),
        "agent:validator": Principal(
            identity="agent:validator",
            kind="agent",
            public_key="b" * 64,
            capabilities=frozenset(),
        ),
    }
    values = {
        "repository_id": "repo",
        "policy_id": "p1",
        "root_public_key": "c" * 64,
        "principals": principals,
        "bootstrap_enabled": False,
        "bootstrap_scope": frozenset(),
        "genesis_digest": "sha256:" + "0" * 64,
    }
    values.update(overrides)
    return AuthorityPolicy(**values)


def test_trust_root_inside_the_runs_root_refuses(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    staged = runs / "r1" / "evidence" / "genesis.tessera.json"
    staged.parent.mkdir(parents=True)
    staged.write_text("{}", encoding="utf-8")
    with pytest.raises(AuthorityVerificationError, match="operator-owned"):
        require_operator_owned_trust_root(staged, runs)
    with pytest.raises(AuthorityVerificationError, match="operator-owned"):
        require_operator_owned_trust_root(runs, runs)


def test_trust_root_outside_the_runs_root_is_accepted(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    genesis = tmp_path / "authority" / "genesis.tessera.json"
    genesis.parent.mkdir()
    genesis.write_text("{}", encoding="utf-8")
    require_operator_owned_trust_root(genesis, runs)  # no raise


def test_human_public_keys_selects_exactly_the_enrolled_humans() -> None:
    keys = human_public_keys(_policy())
    assert keys == frozenset({"a" * 64})  # the agent key is not in the forbidden set


def test_host_signing_seam_refuses_an_enrolled_human_key(tmp_path: Path) -> None:
    """The wrap_json rail, exercised end-to-end when the real tessera binary is
    available (the same gating make test-tessera uses); the refusal must also
    remove the minted envelope so nothing forged survives on disk."""
    import os
    import shutil
    import subprocess

    from factory_runtime.tessera import TesseraCli, TesseraVerificationError

    tessera_bin = os.environ.get("FACTORY_TESSERA_BIN") or shutil.which("tessera")
    if not tessera_bin or not Path(tessera_bin).exists():
        pytest.skip("tessera binary not available (set FACTORY_TESSERA_BIN)")

    key_path = tmp_path / "human.key"
    keygen = subprocess.run(
        [tessera_bin, "keygen", "--output", str(key_path)],
        capture_output=True,
        text=True,
    )
    if keygen.returncode != 0 or not key_path.exists():
        pytest.skip(f"tessera keygen unavailable: {keygen.stderr.strip()[:120]}")

    cli = TesseraCli((tessera_bin,))
    probe = cli.wrap_json(
        {"probe": True},
        kind="factory-test",
        key_path=key_path,
        output_path=tmp_path / "probe.tessera.json",
    )
    forbidden = frozenset({probe.public_key})
    output = tmp_path / "forged.tessera.json"
    with pytest.raises(TesseraVerificationError, match="enrolled-human key"):
        cli.wrap_json(
            {"authority": "forged"},
            kind="factory-test",
            key_path=key_path,
            output_path=output,
            forbidden_signer_public_keys=forbidden,
        )
    assert not output.exists()  # nothing forged survives on disk
