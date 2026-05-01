"""Tests for the app-level telemetry collector factory.

``_build_telemetry_collector`` builds a :class:`TelemetryCollector`
wired against the memory-dir env var. The collector respects
``SYNTHORG_TELEMETRY_ENABLED`` internally; this module verifies the
path derivation, env-var handling, and memory-dir validation around
construction.
"""

from pathlib import Path

import pytest

from synthorg.api.app_builders import _build_telemetry_collector
from synthorg.telemetry.collector import TelemetryCollector


@pytest.mark.unit
class TestBuildTelemetryCollector:
    """Env-var handling and data-dir derivation."""

    def test_defaults_to_container_path_when_env_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_MEMORY_DIR", raising=False)
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        assert isinstance(collector, TelemetryCollector)
        assert collector._data_dir == Path("/data/telemetry")
        assert collector.enabled is False

    def test_derives_sibling_dir_from_memory_dir(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        memory_dir = tmp_path / "memory"
        monkeypatch.setenv("SYNTHORG_MEMORY_DIR", str(memory_dir))
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        assert collector._data_dir == tmp_path / "telemetry"

    async def test_opt_in_flips_enabled_via_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from synthorg.telemetry.config import (
            TelemetryBackend,
            TelemetryConfig,
        )

        monkeypatch.setenv("SYNTHORG_MEMORY_DIR", str(tmp_path / "memory"))
        monkeypatch.setenv("SYNTHORG_TELEMETRY_ENABLED", "true")
        # Use the NOOP backend so this test exercises the
        # enable-flag plumbing + deployment-id directory creation
        # without crossing the LOGFIRE token-embedded gate. The
        # token-missing ERROR path has its own dedicated coverage
        # in tests/unit/telemetry/test_collector.py.
        collector = _build_telemetry_collector(
            TelemetryConfig(backend=TelemetryBackend.NOOP),
        )
        try:
            assert collector.enabled is True
            # ``start()`` performs the deployment-id load via
            # ``asyncio.to_thread``; the directory is materialised
            # there, not in ``__init__``.
            await collector.start()
            assert (tmp_path / "telemetry").exists()
        finally:
            await collector.shutdown()


@pytest.mark.unit
class TestMemoryDirValidation:
    """Reject junk ``SYNTHORG_MEMORY_DIR`` values before deriving paths.

    ``memory_dir.parent / "telemetry"`` is only meaningful when the
    env var is an absolute container path; empty, whitespace-only,
    or relative values would land the deployment ID under
    ``/telemetry`` or a cwd-relative directory and silently miss
    the data volume. The builder falls back to ``/data/memory``
    (its default) in each of those cases so misconfiguration is
    observable via logs but does not poison persistence.
    """

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param("", id="empty_string"),
            pytest.param("   ", id="whitespace_only"),
            pytest.param("\t\n", id="tabs_and_newlines"),
            pytest.param("relative/path", id="relative_subdir"),
            pytest.param("./memory", id="dot_relative"),
            pytest.param("memory", id="bare_name"),
        ],
    )
    def test_invalid_values_fall_back_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        bad_value: str,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_MEMORY_DIR", bad_value)
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        # Falls back to ``/data/memory`` -> ``/data/telemetry``.
        assert collector._data_dir == Path("/data/telemetry")

    def test_absolute_path_with_whitespace_is_trimmed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            "SYNTHORG_MEMORY_DIR",
            f"  {tmp_path / 'memory'}  ",
        )
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        # Surrounding whitespace is stripped before the prefix check.
        assert collector._data_dir == tmp_path / "telemetry"

    def test_path_outside_allowed_roots_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Absolute paths outside ``/data`` + temp dir are rejected.

        CodeQL's ``py/path-injection`` rule treats the env var as
        untrusted input; the builder caps the allow-list to the
        container data volume and the OS temp dir so a hostile or
        typo'd value (e.g. ``/etc``) cannot steer deployment-ID
        writes outside the intended surface.
        """
        monkeypatch.setenv("SYNTHORG_MEMORY_DIR", "/etc/synthorg")
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        assert collector._data_dir == Path("/data/telemetry")

    def test_traversal_via_dotdot_is_canonicalised_and_checked(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`/data/../etc` gets rejected via the ``..`` traversal guard.

        The explicit ``Path.parts`` check catches traversal attempts
        before the prefix check, so ``/data/../etc`` cannot smuggle
        past the allow-list by starting with ``/data``.
        """
        monkeypatch.setenv("SYNTHORG_MEMORY_DIR", "/data/../etc/memory")
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        assert collector._data_dir == Path("/data/telemetry")

    def test_path_equal_to_root_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``SYNTHORG_MEMORY_DIR=/data`` must fall back to the default.

        Regression guard: ``_build_telemetry_collector`` derives
        ``memory_dir.parent / "telemetry"``. If the memory dir
        equals a root (``/data``), the parent is ``/`` and the
        telemetry dir would escape to ``/telemetry``. The
        allow-list therefore requires the memory dir to be a
        *strict* descendant of a root.
        """
        monkeypatch.setenv("SYNTHORG_MEMORY_DIR", "/data")
        monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
        collector = _build_telemetry_collector()
        assert collector._data_dir == Path("/data/telemetry")
