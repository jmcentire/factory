"""TargetManifest loader tests — accepts a valid manifest, rejects malformed input, and
refuses any code reference (data only, never a code import)."""

from __future__ import annotations

import pytest

from factory_core.target import TargetManifestError, load_target_manifest
from tests.conftest import SYNTHETIC_TARGET

VALID = """\
schema_version = "factory-target-manifest/1"
target_id = "acme"

[repo]
url = "https://example.invalid/acme/widget.git"
ref = "main"

[adapters]
repo = "readonly_git"
knowledge = "kin_reader"
compliance = "rules_json"
idp = "oidc"
artifact_sink = "local_fs"

[compliance]
rules_path = "compliance/rules.json"
"""


def _write(tmp_path, text: str):
    p = tmp_path / "target.toml"
    p.write_text(text)
    return p


# --------------------------------------------------------------------------- #
# Accept
# --------------------------------------------------------------------------- #

def test_accepts_the_synthetic_empty_target() -> None:
    tm = load_target_manifest(SYNTHETIC_TARGET)
    assert tm.target_id == "synthetic-empty"
    assert set(tm.adapters) == {"repo", "knowledge", "compliance", "idp", "artifact_sink"}
    assert tm.content_digest.startswith("sha256:")
    assert tm.source_digest.startswith("sha256:")
    assert len(tm.roles) == 2


def test_accepts_a_minimal_valid_manifest(tmp_path) -> None:
    tm = load_target_manifest(_write(tmp_path, VALID))
    assert tm.target_id == "acme"
    assert tm.repo["ref"] == "main"


# --------------------------------------------------------------------------- #
# Reject malformed
# --------------------------------------------------------------------------- #

def test_rejects_malformed_toml(tmp_path) -> None:
    with pytest.raises(TargetManifestError, match="not valid TOML"):
        load_target_manifest(_write(tmp_path, "this is = = not toml ["))


def test_rejects_missing_required_section(tmp_path) -> None:
    broken = VALID.replace(
        '[repo]\nurl = "https://example.invalid/acme/widget.git"\nref = "main"\n', ""
    )
    with pytest.raises(TargetManifestError, match="schema violation"):
        load_target_manifest(_write(tmp_path, broken))


def test_rejects_unknown_top_level_key(tmp_path) -> None:
    with pytest.raises(TargetManifestError, match="schema violation"):
        load_target_manifest(_write(tmp_path, VALID + '\nrogue_field = "nope"\n'))


# --------------------------------------------------------------------------- #
# Refuse code references (the boundary guarantee: data only, never code)
# --------------------------------------------------------------------------- #

def test_refuses_adapter_that_is_an_import_path(tmp_path) -> None:
    bad = VALID.replace('repo = "readonly_git"', 'repo = "target_packs.acme.repo:RepoAdapter"')
    with pytest.raises(TargetManifestError) as exc:
        load_target_manifest(_write(tmp_path, bad))
    assert "code reference" in str(exc.value) or "registered adapter name" in str(exc.value)


def test_refuses_dotted_callable_anywhere_in_the_manifest(tmp_path) -> None:
    bad = VALID.replace('rules_path = "compliance/rules.json"',
                        'rules_path = "acme.rules:load"')
    with pytest.raises(TargetManifestError, match="code reference"):
        load_target_manifest(_write(tmp_path, bad))


def test_refuses_python_file_reference(tmp_path) -> None:
    bad = VALID.replace('rules_path = "compliance/rules.json"',
                        'rules_path = "acme/evil.py"')
    with pytest.raises(TargetManifestError, match="code reference"):
        load_target_manifest(_write(tmp_path, bad))


# --------------------------------------------------------------------------- #
# Content address / signature (fail-closed before adapter resolution)
# --------------------------------------------------------------------------- #

def test_declared_content_digest_must_match(tmp_path) -> None:
    signed = VALID + '\n[signature]\ncontent_digest = "sha256:deadbeef"\n'
    with pytest.raises(TargetManifestError, match="content-address mismatch"):
        load_target_manifest(_write(tmp_path, signed))


def test_content_address_round_trip(tmp_path) -> None:
    unsigned = load_target_manifest(_write(tmp_path, VALID))
    signed = VALID + f'\n[signature]\ncontent_digest = "{unsigned.content_digest}"\n'
    p = tmp_path / "signed.toml"
    p.write_text(signed)
    tm = load_target_manifest(p)
    assert tm.content_digest == unsigned.content_digest


def test_require_signature_fails_closed_without_a_verifiable_signature(tmp_path) -> None:
    with pytest.raises(TargetManifestError):
        load_target_manifest(_write(tmp_path, VALID), require_signature=True)
