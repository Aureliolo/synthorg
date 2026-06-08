"""Unit tests for the in-process provisioned-environment memo."""

from datetime import UTC, datetime

import pytest

from synthorg.core.project_enums import EnvironmentType
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.hash_cache import (
    ProvisionedEnvironmentCache,
)

pytestmark = pytest.mark.unit


def _env(
    project_id: str = "proj-1", declaration_hash: str = "a" * 64
) -> ProjectEnvironment:
    ts = datetime(2026, 5, 21, tzinfo=UTC)
    return ProjectEnvironment(
        project_id=NotBlankStr(project_id),
        environment_type=EnvironmentType.MANIFEST,
        declaration_hash=NotBlankStr(declaration_hash),
        provisioned_at=ts,
        updated_at=ts,
    )


class TestProvisionedEnvironmentCache:
    def test_get_missing_returns_none(self) -> None:
        assert ProvisionedEnvironmentCache().get("proj-1") is None

    def test_set_then_get(self) -> None:
        cache = ProvisionedEnvironmentCache()
        env = _env()
        cache.set("proj-1", env)
        assert cache.get("proj-1") == env

    def test_set_overwrites(self) -> None:
        cache = ProvisionedEnvironmentCache()
        cache.set("proj-1", _env(declaration_hash="a" * 64))
        newer = _env(declaration_hash="b" * 64)
        cache.set("proj-1", newer)
        assert cache.get("proj-1") == newer

    def test_invalidate(self) -> None:
        cache = ProvisionedEnvironmentCache()
        cache.set("proj-1", _env())
        cache.invalidate("proj-1")
        assert cache.get("proj-1") is None

    def test_invalidate_missing_is_noop(self) -> None:
        cache = ProvisionedEnvironmentCache()
        cache.invalidate("ghost")  # no raise
        assert cache.get("ghost") is None

    def test_isolated_per_project(self) -> None:
        cache = ProvisionedEnvironmentCache()
        a, b = _env("proj-1"), _env("proj-2")
        cache.set("proj-1", a)
        cache.set("proj-2", b)
        assert cache.get("proj-1") == a
        assert cache.get("proj-2") == b
