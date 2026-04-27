"""Source-of-resolution audit log on first cold read.

Every ``(namespace, key)`` resolves through the source chain at most
once per process at INFO; subsequent resolutions stay at DEBUG.  The
log payload carries ``source`` and ``yaml_path`` so an operator can
tell which surface supplied each value at startup and where the YAML
counterpart lives.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog
from pydantic import BaseModel, ConfigDict

from synthorg.observability.events.settings import SETTINGS_VALUE_RESOLVED
from synthorg.persistence.settings_protocol import SettingsRepository
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


class _Logging(BaseModel):
    model_config = ConfigDict(frozen=True)
    root_level: str = "info"


class _FakeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    logging: _Logging = _Logging()


def _definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="root_log_level",
        type=SettingType.ENUM,
        default="info",
        description="Root logger level",
        group="Logging",
        enum_values=("debug", "info", "warning", "error", "critical"),
        yaml_path="logging.root_level",
    )


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    return SettingsService(
        repository=repo,
        registry=registry,
        config=_FakeConfig(),
    )


def _resolved(logs: Any) -> list[dict[str, Any]]:
    return [dict(log) for log in logs if log["event"] == SETTINGS_VALUE_RESOLVED]


async def test_first_cold_read_emits_info(service: SettingsService) -> None:
    with structlog.testing.capture_logs() as logs:
        await service.get("observability", "root_log_level")
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert len(info_events) == 1, f"expected one INFO event, got {events}"
    e = info_events[0]
    assert e["namespace"] == "observability"
    assert e["key"] == "root_log_level"
    assert e["source"] == "yaml"
    assert e["yaml_path"] == "logging.root_level"


async def test_subsequent_reads_stay_at_debug(service: SettingsService) -> None:
    # First call promotes the (ns,key) into the seen-set.
    await service.get("observability", "root_log_level")
    with structlog.testing.capture_logs() as logs:
        for _ in range(5):
            await service.get("observability", "root_log_level")
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert info_events == [], f"second-read INFO leak: {info_events}"


async def test_concurrent_first_reads_emit_info_at_most_once(
    service: SettingsService,
) -> None:
    with structlog.testing.capture_logs() as logs:
        async with asyncio.TaskGroup() as tg:
            for _ in range(10):
                tg.create_task(service.get("observability", "root_log_level"))
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    # asyncio cooperative concurrency cannot interleave the membership
    # check + set add (no awaits between). Even with 10 concurrent
    # readers the event fires exactly once.
    assert len(info_events) == 1, f"concurrent first-read race: {info_events}"


# ── Source coverage: db / env / default ────────────────────────


async def test_db_source_logged() -> None:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=("error", "2026-04-27T00:00:00Z"))
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    svc = SettingsService(
        repository=repo,
        registry=registry,
        config=_FakeConfig(),
    )
    with structlog.testing.capture_logs() as logs:
        await svc.get("observability", "root_log_level")
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert len(info_events) == 1
    assert info_events[0]["source"] == "db"


async def test_env_source_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    # Empty config so YAML resolution returns None and env wins.
    empty_config: Any = _FakeConfig.model_construct()
    svc = SettingsService(
        repository=repo,
        registry=registry,
        config=empty_config,
    )
    monkeypatch.setenv("SYNTHORG_OBSERVABILITY_ROOT_LOG_LEVEL", "warning")
    with structlog.testing.capture_logs() as logs:
        await svc.get("observability", "root_log_level")
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert len(info_events) == 1
    assert info_events[0]["source"] == "env"


async def test_default_source_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drop yaml_path so the YAML resolver can't supply a value.
    defn = _definition().model_copy(update={"yaml_path": None})
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.get_all = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(defn)
    svc = SettingsService(
        repository=repo,
        registry=registry,
        config=_FakeConfig(),
    )
    monkeypatch.delenv("SYNTHORG_OBSERVABILITY_ROOT_LOG_LEVEL", raising=False)
    with structlog.testing.capture_logs() as logs:
        await svc.get("observability", "root_log_level")
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert len(info_events) == 1
    assert info_events[0]["source"] == "default"
