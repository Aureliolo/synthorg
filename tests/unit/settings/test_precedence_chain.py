"""Precedence-chain coverage across mutable types and cache isolation.

The DB > env > YAML > default chain is the project-wide contract for
every mutable setting (see ``docs/reference/configuration-precedence.md``
and the MANDATORY section in ``CLAUDE.md``).  The existing
``TestResolutionOrder`` suite covers the float case; this file
parametrizes the chain across enum, int, and float types in a single
sweep so any regression in :class:`SettingsService` resolution is
caught for every numeric path that the registry actually uses.

A separate sub-suite verifies that cache state stays isolated from the
env / YAML / default paths: the cache is DB-only by design, and a
non-DB resolution must not pollute it (otherwise a transient YAML or
env value would persist after the underlying source disappeared).
"""

from collections.abc import Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.enums import SettingNamespace, SettingSource, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


# ── Fake config tree mirroring the registered yaml_paths below. ──


class _NestedFloat(BaseModel):
    model_config = ConfigDict(frozen=True)
    f_value: float = 1.5


class _NestedInt(BaseModel):
    model_config = ConfigDict(frozen=True)
    i_value: int = 10


class _NestedEnum(BaseModel):
    model_config = ConfigDict(frozen=True)
    e_value: str = "info"


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True)
    nested_float: _NestedFloat = _NestedFloat()
    nested_int: _NestedInt = _NestedInt()
    nested_enum: _NestedEnum = _NestedEnum()


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    section: _Section = _Section()


# ── Per-type case bundle ─────────────────────────────────────────


@dataclass(frozen=True)
class _Case:
    """Bundle of parameters for a single per-type precedence sweep."""

    label: str
    factory: Callable[[], SettingDefinition]
    namespace: str
    key: str
    env_value: str
    db_value: str
    default: str

    def env_var(self) -> str:
        return f"SYNTHORG_{self.namespace.upper()}_{self.key.upper()}"


def _enum_definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="log_level",
        type=SettingType.ENUM,
        default="warning",
        description="Log level under test",
        group="Test",
        enum_values=("debug", "info", "warning", "error"),
        yaml_path="section.nested_enum.e_value",
    )


def _int_definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.API,
        key="rate_limit_value",
        type=SettingType.INTEGER,
        default="3",
        description="Integer under test",
        group="Test",
        min_value=0,
        max_value=1000,
        yaml_path="section.nested_int.i_value",
    )


def _float_definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.ENGINE,
        key="approval_timeout",
        type=SettingType.FLOAT,
        default="60.0",
        description="Float under test",
        group="Test",
        min_value=0.0,
        max_value=10000.0,
        yaml_path="section.nested_float.f_value",
    )


_CASES: tuple[_Case, ...] = (
    _Case(
        label="enum",
        factory=_enum_definition,
        namespace="observability",
        key="log_level",
        env_value="debug",
        db_value="error",
        default="warning",
    ),
    _Case(
        label="int",
        factory=_int_definition,
        namespace="api",
        key="rate_limit_value",
        env_value="99",
        db_value="777",
        default="3",
    ),
    _Case(
        label="float",
        factory=_float_definition,
        namespace="engine",
        key="approval_timeout",
        env_value="12.5",
        db_value="1234.5",
        default="60.0",
    ),
)


def _service_with(definition: SettingDefinition) -> tuple[SettingsService, AsyncMock]:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(definition)
    svc = SettingsService(
        repository=repo,
        registry=registry,
        config=_FakeConfig(),
    )
    return svc, repo


# ── Chain-per-type sweep ─────────────────────────────────────────


@pytest.mark.parametrize("case", _CASES, ids=[c.label for c in _CASES])
async def test_default_when_no_overrides(
    case: _Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No DB, no env; YAML model fields exist with their built-in
    # defaults, which take precedence over the registry default. To
    # exercise the registry-default branch we point yaml_path at a
    # missing attribute so the YAML resolver returns None.
    defn = case.factory().model_copy(update={"yaml_path": "section.does_not_exist"})
    svc, _ = _service_with(defn)
    monkeypatch.delenv(case.env_var(), raising=False)
    result = await svc.get(case.namespace, case.key)
    assert result.value == case.default
    assert result.source == SettingSource.DEFAULT


@pytest.mark.parametrize("case", _CASES, ids=[c.label for c in _CASES])
async def test_yaml_beats_default(case: _Case) -> None:
    svc, _ = _service_with(case.factory())
    result = await svc.get(case.namespace, case.key)
    assert result.source == SettingSource.YAML


@pytest.mark.parametrize("case", _CASES, ids=[c.label for c in _CASES])
async def test_env_beats_yaml(case: _Case, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = _service_with(case.factory())
    monkeypatch.setenv(case.env_var(), case.env_value)
    result = await svc.get(case.namespace, case.key)
    assert result.value == case.env_value
    assert result.source == SettingSource.ENVIRONMENT


@pytest.mark.parametrize("case", _CASES, ids=[c.label for c in _CASES])
async def test_db_beats_env_and_yaml(
    case: _Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, repo = _service_with(case.factory())
    monkeypatch.setenv(case.env_var(), case.env_value)
    repo.get.return_value = (case.db_value, "2026-03-16T10:00:00Z")
    result = await svc.get(case.namespace, case.key)
    assert result.value == case.db_value
    assert result.source == SettingSource.DATABASE


# ── Cache isolation ─────────────────────────────────────────────


async def test_yaml_resolution_does_not_populate_db_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cache stores only DB-resolved values. After two YAML reads
    # the repository must still see two queries -- otherwise a
    # transient YAML override could persist and outlive its source.
    svc, repo = _service_with(_float_definition())
    monkeypatch.delenv("SYNTHORG_ENGINE_APPROVAL_TIMEOUT", raising=False)
    await svc.get("engine", "approval_timeout")
    await svc.get("engine", "approval_timeout")
    assert repo.get.call_count == 2


async def test_env_resolution_does_not_populate_db_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, repo = _service_with(_int_definition())
    monkeypatch.setenv("SYNTHORG_API_RATE_LIMIT_VALUE", "42")
    await svc.get("api", "rate_limit_value")
    await svc.get("api", "rate_limit_value")
    assert repo.get.call_count == 2
