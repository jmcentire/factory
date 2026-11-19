"""A packaged Factory identifies its revision from the packager's stamp, not from Git."""

import pytest

from factory_runtime import host_git, version

REVISION = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def stamp(tmp_path, monkeypatch):
    package = tmp_path / "factory_runtime"
    package.mkdir()
    monkeypatch.setattr(version, "__file__", str(package / "version.py"))
    return package / "BUILD_REVISION"


@pytest.mark.parametrize("revision", [REVISION, REVISION + "89abcdef" * 3])
def test_a_stamped_build_reports_its_revision_without_running_git(stamp, monkeypatch, revision):
    def no_git(*arguments, **options):
        raise AssertionError("a stamped build must not ask Git for its identity")

    monkeypatch.setattr(host_git, "run_host_git", no_git)
    stamp.write_text(revision + "\n")
    identity = version.build_identity()
    assert (identity["revision"], identity["dirty"]) == (revision, False)
    assert revision in version.format_version()
    assert "dirty" not in version.format_version()


@pytest.mark.parametrize("content", [
    b"", b"\n", REVISION[:7].encode() + b"\n", REVISION.upper().encode() + b"\n", b"v0.8.30\n",
    REVISION.encode() + b"\n\n", REVISION.encode() + b"\ndirty\n", b" " + REVISION.encode() + b"\n",
    b"\xff",
])
def test_a_stamp_that_is_not_one_full_revision_is_refused_by_name(stamp, content):
    stamp.write_bytes(content)
    with pytest.raises(ValueError, match="BUILD_REVISION must hold one full lowercase Git"):
        version.build_identity()


def test_without_a_stamp_a_package_reports_unknown(stamp):
    identity = version.build_identity()
    assert (identity["revision"], identity["dirty"]) == (None, None)
