"""Unit tests for the startup binary preflight.

The defect this guards shipped a backend image with no ``git``, so every
dispatch died at workspace provisioning and the whole test suite was
blind to it because tests run where ``git`` is on PATH. These tests fake
PATH resolution so the missing-binary case is exercised on a machine that
has the binaries.
"""

from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.api import construction_phase
from synthorg.api.app_overrides import AppOverrides
from synthorg.api.boot_persistence import BootPersistence
from synthorg.api.config import ApiConfig
from synthorg.api.construction_phase import build_construction_services
from synthorg.api.lifecycle_helpers.binary_preflight import (
    BINARY_MANIFEST,
    BinaryRecord,
    RequiredBinaryMissingError,
    required_binaries_for,
    run_binary_preflight,
)
from synthorg.config.schema import RootConfig

pytestmark = pytest.mark.unit

_MODULE = "synthorg.api.lifecycle_helpers.binary_preflight"


def _resolving(*present: str) -> AbstractContextManager[object]:
    """Patch PATH lookup so only *present* binaries resolve."""

    def _which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return patch(f"{_MODULE}.shutil.which", side_effect=_which)


def _reporting(version: str | None) -> AbstractContextManager[object]:
    """Patch the version probe so a test never reads the host's own toolchain.

    Without this the git floor is checked against whatever git the machine
    happens to have, so the suite would pass or fail on a property of the
    developer's box rather than on the code under test.
    """
    parsed = None if version is None else tuple(int(p) for p in version.split("."))
    return patch(f"{_MODULE}.installed_version", return_value=parsed)


def _names(records: Iterable[BinaryRecord]) -> set[str]:
    return {record.name for record in records}


def _every_name() -> Iterator[str]:
    return (record.name for record in BINARY_MANIFEST)


class TestManifest:
    def test_git_is_listed(self) -> None:
        """Workspace provisioning is on the critical path of every dispatch."""
        assert "git" in _names(BINARY_MANIFEST)

    def test_no_docker_entry(self) -> None:
        """The image builder runs through aiodocker, so no CLI is shelled out."""
        assert "docker" not in _names(BINARY_MANIFEST)

    def test_no_self_provisioning_binary_is_listed(self) -> None:
        """A binary the backend downloads on demand is not missing at boot.

        Both tunnel adapters fetch their vendor CLI on first start and report
        their own live availability (including when that download is switched
        off), so demanding them here would refuse to acknowledge a fetchable
        binary and duplicate a better report.
        """
        assert _names(BINARY_MANIFEST).isdisjoint({"cloudflared", "devtunnel", "nix"})

    def test_every_record_names_its_package_and_consumers(self) -> None:
        """A preflight failure has to be actionable without reading the code."""
        for record in BINARY_MANIFEST:
            assert record.package.strip()
            assert record.consumers

    def test_a_record_with_nothing_to_say_is_refused(self) -> None:
        """The record's fields ARE the operator's message, so none may be blank."""
        with pytest.raises(ValueError, match="must not be blank"):
            BinaryRecord(name=" ", package="git", consumers=("something",))
        with pytest.raises(ValueError, match="must not be blank"):
            BinaryRecord(name="git", package="", consumers=("something",))
        with pytest.raises(ValueError, match="at least one non-blank consumer"):
            BinaryRecord(name="git", package="git", consumers=())

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
        with _resolving(*_every_name()), _reporting("2.48"):
            run_binary_preflight(backend_name="postgres")

    def test_postgres_tools_do_not_block_a_sqlite_boot(self) -> None:
        """A SQLite deployment never shells out to the Postgres tools."""
        with _resolving("git"), _reporting("2.48"):
            run_binary_preflight(backend_name="sqlite")


class TestVersionFloor:
    """Present on PATH is not the same as able to do the job."""

    def test_a_git_below_the_floor_refuses_the_boot(self) -> None:
        """git ignores an unknown config key instead of refusing it.

        So an old binary accepts ``worktree.useRelativePaths``, reports
        nothing, and hands back a worktree recording the backend's absolute
        path. The agent opens it through a different mount and every git
        command it runs fails. Refused at boot because the alternative first
        report is a failing agent deep inside a sandbox.
        """
        with (
            _resolving(*_every_name()),
            _reporting("2.47.1"),
            pytest.raises(RequiredBinaryMissingError) as excinfo,
        ):
            run_binary_preflight(backend_name="sqlite")

        message = str(excinfo.value)
        assert "2.47.1" in message
        assert "2.48" in message
        assert "useRelativePaths" in message

    def test_the_floor_itself_passes(self) -> None:
        with _resolving(*_every_name()), _reporting("2.48"):
            run_binary_preflight(backend_name="sqlite")

    def test_a_longer_version_is_compared_on_the_shared_prefix(self) -> None:
        """``2.55.0.windows.3`` is not below ``2.48``."""
        with _resolving(*_every_name()), _reporting("2.55.0.3"):
            run_binary_preflight(backend_name="sqlite")

    def test_an_unreadable_version_does_not_refuse_the_boot(self) -> None:
        """Not knowing the version is not evidence of an old one.

        Refusing here would take a working deployment down over output this
        parser did not anticipate, which is a worse failure than the one the
        floor exists to prevent.
        """
        with _resolving(*_every_name()), _reporting(None):
            run_binary_preflight(backend_name="sqlite")

    def test_a_floor_without_a_reason_is_refused(self) -> None:
        """The reason is rendered into the refusal, so it cannot be blank."""
        with pytest.raises(ValueError, match="version_reason"):
            BinaryRecord(
                name="git",
                package="git",
                consumers=("workspace provisioning",),
                min_version=(2, 48),
            )


class TestItRunsAtBoot:
    """The half a well-tested pure function does not buy.

    The defect was an image that booted cleanly and could not dispatch, so
    what has to hold is that the boot path actually asks: a preflight
    nothing calls is the same image with a longer test suite.
    """

    @staticmethod
    def _build(tmp_path: Path) -> None:
        """Run the construction phase over an otherwise-valid boot bundle."""
        build_construction_services(
            effective_config=RootConfig(company_name="test-co"),
            api_config=ApiConfig(),
            overrides=AppOverrides(),
            boot=BootPersistence(
                persistence=None,
                artifact_storage=None,
                resolved_db_path=None,
                resolved_config_path=tmp_path / "company.yaml",
                db_url="",
                db_path="",
            ),
        )

    def test_construction_refuses_a_boot_without_git(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        called: list[str] = []

        def _refuse(*, backend_name: str) -> None:
            called.append(backend_name)
            msg = "'git' is not on PATH"
            raise RequiredBinaryMissingError(msg)

        monkeypatch.setattr(construction_phase, "run_binary_preflight", _refuse)

        with pytest.raises(RequiredBinaryMissingError, match="git"):
            self._build(tmp_path)

        # Called with the resolved backend, and with none resolved yet that
        # is the empty string: only the backend-independent binaries can be
        # demanded at this point in the boot.
        assert called == [""]

    def test_a_present_toolchain_lets_the_boot_proceed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The other direction, so the test above cannot pass by construction
        # happening to fail for an unrelated reason.
        calls: list[str] = []

        def _accept(*, backend_name: str) -> None:
            calls.append(backend_name)

        monkeypatch.setattr(construction_phase, "run_binary_preflight", _accept)

        self._build(tmp_path)

        assert calls == [""]
