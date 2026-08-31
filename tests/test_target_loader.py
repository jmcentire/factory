"""TargetManifest loader tests — accepts a valid manifest, rejects malformed input, and
refuses any code reference (data only, never a code import)."""

from __future__ import annotations

import pytest

from factory_core.target import TargetManifestError, load_target_manifest
from tests.conftest import SYNTHETIC_TARGET

VALID = """\
schema_version = "factory-target-manifest/2"
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

[build]
pattern_catalog_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
max_attempts = 2
construction_modes = ["regenerate", "brownfield"]

[build.signal]
signal_pass_deadline = 2
signal_pass_warn = 1
signal_wall_clock_cap_hours = 24
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
    assert tm.source_digest.startswith("sha256:")
    assert len(tm.roles) == 2


def test_accepts_a_minimal_valid_manifest(tmp_path) -> None:
    tm = load_target_manifest(_write(tmp_path, VALID))
    assert tm.target_id == "acme"
    assert tm.repo["ref"] == "main"
    assert tm.build["max_attempts"] == 2


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
    bad = VALID.replace('rules_path = "compliance/rules.json"', 'rules_path = "acme.rules:load"')
    with pytest.raises(TargetManifestError, match="code reference"):
        load_target_manifest(_write(tmp_path, bad))


def test_refuses_python_file_reference(tmp_path) -> None:
    bad = VALID.replace('rules_path = "compliance/rules.json"', 'rules_path = "acme/evil.py"')
    with pytest.raises(TargetManifestError, match="code reference"):
        load_target_manifest(_write(tmp_path, bad))


# --------------------------------------------------------------------------- #
# Content address / signature (fail-closed before adapter resolution)
# --------------------------------------------------------------------------- #


def test_schema_1_manifest_refuses_at_parse(tmp_path) -> None:
    """The migration is fail-closed at the earliest firing point: a /1 manifest refuses
    at schema validation — re-declaration under /2, never a legacy-acceptance path."""
    legacy = VALID.replace("factory-target-manifest/2", "factory-target-manifest/1")
    with pytest.raises(TargetManifestError, match="schema violation"):
        load_target_manifest(_write(tmp_path, legacy))


def test_in_file_content_digest_self_claim_is_refused_by_schema(tmp_path) -> None:
    """2.1: a file cannot vouch for itself — the signature block carries only the key
    id; any in-file digest/value field refuses at schema validation."""
    signed = VALID + '\n[signature]\ncontent_digest = "sha256:deadbeef"\n'
    with pytest.raises(TargetManifestError, match="schema violation"):
        load_target_manifest(_write(tmp_path, signed))


def test_the_single_digest_is_over_the_raw_bytes(tmp_path) -> None:
    """Two manifests with identical logical content but different bytes (a comment)
    address DIFFERENTLY: the digest binds what was read, not what the parser produced."""
    from factory_core.manifest import digest_bytes

    a = load_target_manifest(_write(tmp_path, VALID))
    commented = VALID + "\n# a comment changes the bytes, not the parse\n"
    q = tmp_path / "commented.toml"
    q.write_text(commented)
    b = load_target_manifest(q)
    assert a.source_digest == digest_bytes(VALID.encode())
    assert b.source_digest == digest_bytes(commented.encode())
    assert a.source_digest != b.source_digest


def test_require_signature_fails_closed_without_a_key_id(tmp_path) -> None:
    with pytest.raises(TargetManifestError, match="no signing key id"):
        load_target_manifest(_write(tmp_path, VALID), require_signature=True)


def test_signature_seam_verifies_over_the_raw_bytes(tmp_path) -> None:
    """The dormant trust-root seam receives the RAW bytes — never a canonical
    re-encoding — and its refusal fail-closes the load."""
    signed = VALID + '\n[signature]\nkey_id = "founder-2026"\n'
    path = _write(tmp_path, signed)
    seen: dict[str, object] = {}

    def check(raw: bytes, block: dict) -> bool:
        seen["raw"] = raw
        seen["block"] = block
        return True

    tm = load_target_manifest(path, require_signature=True, verify_signature=check)
    assert seen["raw"] == signed.encode()
    assert seen["block"] == {"key_id": "founder-2026"}
    assert tm.source_digest.startswith("sha256:")

    def refuse(raw: bytes, block: dict) -> bool:
        return False

    with pytest.raises(TargetManifestError, match="signature verification failed"):
        load_target_manifest(path, require_signature=True, verify_signature=refuse)
