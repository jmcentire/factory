"""A packaged Factory identifies its revision from the packager's stamp, not from Git."""

import pytest

from factory_runtime import host_git, version

REVISION = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def stamp(tmp_path, monkeypatch):
    path = tmp_path / "BUILD_REVISION"
    monkeypatch.setattr(version, "STAMP", path)
    return path


def test_the_shipped_stamp_location_is_beside_the_runtime_package():
    assert version.STAMP.parent.name == "factory_runtime"
    assert version.STAMP.name == "BUILD_REVISION"


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
    "", "\n", REVISION[:7] + "\n", REVISION.upper() + "\n", "v0.8.30\n",
    REVISION + "\n\n", REVISION + "\ndirty\n", " " + REVISION + "\n",
])
def test_a_stamp_that_is_not_one_full_revision_is_refused_by_name(stamp, content):
    stamp.write_text(content)
    with pytest.raises(ValueError, match="BUILD_REVISION must hold one full lowercase Git"):
        version.build_identity()


def test_without_a_stamp_the_checkout_answers(stamp):
    # The fixture points at a file that does not exist; this checkout's own Git state applies.
    assert not stamp.exists()
    identity = version.build_identity()
    assert identity["revision"] != REVISION and isinstance(identity["dirty"], bool)
