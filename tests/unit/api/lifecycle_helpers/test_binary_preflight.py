"""Unit tests for the startup binary preflight.

The defect this guards shipped a backend image with no ``git``, so every
dispatch died at workspace provisioning and the whole test suite was
blind to it because tests run where ``git`` is on PATH. These tests fake
PATH resolution so the missing-binary case is exercised on a machine that
has the binaries.
"""

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from unittest.mock import patch

import pytest

from synthorg.api.lifecycle_helpers.binary_preflight import (
    BINARY_MANIFEST,
    BinaryRecord,
    BinaryRequirement,
    RequiredBinaryMissingError,
    required_binaries_for,
    run_binary_preflight,
)

pytestmark = pytest.mark.unit

_MODULE = "synthorg.api.lifecycle_helpers.binary_preflight"


def _resolving(*present: str) -> AbstractContextManager[object]:
    """Patch PATH lookup so only *present* binaries resolve."""

    def _which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return patch(f"{_MODULE}.shutil.which", side_effect=_which)


def _names(records: Iterable[BinaryRecord]) -> set[str]:
    return {record.name for record in records}


def _every_name() -> Iterator[str]:
    return (record.name for record in BINARY_MANIFEST)


class TestManifest:
    def test_git_is_required(self) -> None:
        """Workspace provisioning is on the critical path of every dispatch."""
        git = next(record for record in BINARY_MANIFEST if record.name == "git")
        assert git.requirement is BinaryRequirement.REQUIRED

    def test_no_docker_entry(self) -> None:
        """The image builder runs through aiodocker, so no CLI is shelled out."""
        assert "docker" not in _names(BINARY_MANIFEST)

    def test_every_record_names_its_package_and_consumers(self) -> None:
        """A preflight failure has to be actionable without reading the code."""
        for record in BINARY_MANIFEST:
            assert record.package.strip()
            assert record.consumers

    def test_postgres_tools_are_required_only_on_postgres(self) -> None:
        sqlite_required = _names(required_binaries_for("sqlite"))
        postgres_required = _names(required_binaries_for("postgres"))

        assert "pg_dump" not in sqlite_required
        assert "pg_dump" in postgres_required
        assert "git" in sqlite_required


class TestRequired:
    def test_missing_required_binary_aborts_the_boot(self) -> None:
        with (
            _resolving("pg_dump", "pg_restore"),
            pytest.raises(RequiredBinaryMissingError) as excinfo,
        ):
            run_binary_preflight(backend_name="sqlite")

        assert "git" in str(excinfo.value)

    def test_the_failure_names_the_package_and_the_subsystems(self) -> None:
        """An operator fixing this edits an image manifest, not the code."""
        with _resolving(), pytest.raises(RequiredBinaryMissingError) as excinfo:
            run_binary_preflight(backend_name="sqlite")

        message = str(excinfo.value)
        assert "git" in message
        assert "workspace provisioning" in message.lower()

    def test_present_required_binaries_pass(self) -> None:
        with _resolving(*_every_name()):
            assert run_binary_preflight(backend_name="postgres") == ()

    def test_postgres_tools_do_not_block_a_sqlite_boot(self) -> None:
        """A SQLite deployment never shells out to the Postgres tools."""
        with _resolving("git"):
            missing = run_binary_preflight(backend_name="sqlite")

        # The boot proceeds; only the optional binaries are reported.
        assert _names(missing).isdisjoint({"pg_dump", "pg_restore"})


class TestOptional:
    def test_missing_optional_binary_is_reported_not_raised(self) -> None:
        with _resolving("git"):
            missing = run_binary_preflight(backend_name="sqlite")

        assert "cloudflared" in {record.name for record in missing}

    def test_a_reported_optional_binary_names_its_package(self) -> None:
        """The operator's fix is an image rebuild, so name what to add."""
        with _resolving("git"):
            missing = run_binary_preflight(backend_name="sqlite")

        assert missing
        assert all(record.package.strip() for record in missing)

    def test_nothing_reported_when_everything_resolves(self) -> None:
        with _resolving(*_every_name()):
            assert run_binary_preflight(backend_name="postgres") == ()
