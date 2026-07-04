"""Source-of-resolution audit log.

Every ``(namespace, key)`` resolution logs at DEBUG and carries the winning
``source`` so an operator can tell which surface supplied each value. A
resolution always succeeds, so it never logs at INFO -- problems (a feature
that cannot activate, an unwired dependency) surface through their own
INFO/WARNING events, not here.
"""

import asyncio
from collections.abc import Sequence
from unittest.mock import AsyncMock

import pytest
import structlog
from pydantic import JsonValue
from structlog.typing import EventDict

from synthorg.core.types import NotBlankStr
from synthorg.observability.events.settings import SETTINGS_VALUE_RESOLVED
from synthorg.persistence.settings_protocol import SettingRow, SettingsRepository
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.models import SettingDefinition
from synthorg.settings.registry import SettingsRegistry
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.unit


def _definition() -> SettingDefinition:
    return SettingDefinition(
        namespace=SettingNamespace.OBSERVABILITY,
        key="root_log_level",
        type=SettingType.ENUM,
        default="info",
        description="Root logger level",
        group="Logging",
        enum_values=("debug", "info", "warning", "error", "critical"),
    )


@pytest.fixture
def service() -> SettingsService:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.list_items = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    return SettingsService(
        repository=repo,
        registry=registry,
    )


def _resolved(logs: Sequence[EventDict]) -> list[dict[str, JsonValue]]:
    return [dict(log) for log in logs if log["event"] == SETTINGS_VALUE_RESOLVED]


async def test_cold_read_logs_source_at_debug(
    service: SettingsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SYNTHORG_OBSERVABILITY_ROOT_LOG_LEVEL", raising=False)
    with structlog.testing.capture_logs() as logs:
        await service.get("observability", "root_log_level")
    events = _resolved(logs)
    assert len(events) == 1, f"expected one resolution event, got {events}"
    e = events[0]
    assert e["log_level"] == "debug"
    assert e["namespace"] == "observability"
    assert e["key"] == "root_log_level"
    assert e["source"] == "default"


async def test_resolutions_never_log_at_info(service: SettingsService) -> None:
    with structlog.testing.capture_logs() as logs:
        for _ in range(5):
            await service.get("observability", "root_log_level")
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert info_events == [], f"resolution INFO leak: {info_events}"


async def test_concurrent_reads_never_log_at_info(
    service: SettingsService,
) -> None:
    with structlog.testing.capture_logs() as logs:
        async with asyncio.TaskGroup() as tg:
            for _ in range(10):
                _ = tg.create_task(service.get("observability", "root_log_level"))
    events = _resolved(logs)
    info_events = [e for e in events if e["log_level"] == "info"]
    assert info_events == [], f"concurrent resolution INFO leak: {info_events}"


# ── Source coverage: db / env / default ────────────────────────


async def test_db_source_logged() -> None:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(
        return_value=SettingRow(
            namespace=NotBlankStr("observability"),
            key=NotBlankStr("root_log_level"),
            value="error",
            updated_at="2026-04-27T00:00:00Z",
        )
    )
    repo.get_namespace = AsyncMock(return_value=())
    repo.list_items = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    svc = SettingsService(
        repository=repo,
        registry=registry,
    )
    with structlog.testing.capture_logs() as logs:
        await svc.get("observability", "root_log_level")
    events = _resolved(logs)
    assert len(events) == 1
    assert events[0]["source"] == "db"


async def test_env_source_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.list_items = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    svc = SettingsService(
        repository=repo,
        registry=registry,
    )
    monkeypatch.setenv("SYNTHORG_OBSERVABILITY_ROOT_LOG_LEVEL", "warning")
    with structlog.testing.capture_logs() as logs:
        await svc.get("observability", "root_log_level")
    events = _resolved(logs)
    assert len(events) == 1
    assert events[0]["source"] == "env"


async def test_default_source_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock(spec=SettingsRepository)
    repo.get = AsyncMock(return_value=None)
    repo.get_namespace = AsyncMock(return_value=())
    repo.list_items = AsyncMock(return_value=())
    registry = SettingsRegistry()
    registry.register(_definition())
    svc = SettingsService(
        repository=repo,
        registry=registry,
    )
    monkeypatch.delenv("SYNTHORG_OBSERVABILITY_ROOT_LOG_LEVEL", raising=False)
    with structlog.testing.capture_logs() as logs:
        await svc.get("observability", "root_log_level")
    events = _resolved(logs)
    assert len(events) == 1
    assert events[0]["source"] == "default"
