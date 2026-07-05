"""SERVICE_ABSENT gaps raise an ops signal, never author a tool.

A recurring gap from a wired MCP handler whose backing SynthOrg service is
absent is a framework gap (implement the service), not novel-tool demand. The
service routes it to the operator notification dispatcher and never reaches the
blueprint generator, while a MISSING_TOOL gap still flows to authoring.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.errors import ToolAuthoringError
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore
from synthorg.meta.toolsmith.models import CapabilityGap, GapKind
from synthorg.meta.toolsmith.protocol import ToolBlueprintGenerator
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_CAP = NotBlankStr("text:slugify")
_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _service(
    settings: SettingsService,
    *,
    generator: AsyncMock,
    dispatcher: NotificationDispatcher | None,
) -> ToolsmithService:
    return ToolsmithService(
        config=ToolsmithConfig(
            enabled=True,
            allowed_capabilities=(_CAP,),
            gap_recurrence_threshold=2,
        ),
        gap_store=RingBufferCapabilityGapStore(max_observations=64),
        generator=generator,
        applier=AsyncMock(spec=ToolCreationApplier),
        guards=(),
        config_resolver=ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        ),
        notification_dispatcher=dispatcher,
    )


async def _enable(settings: SettingsService) -> None:
    await settings.set("self_improvement", "tool_creation_enabled", "true")
    await settings.set(
        "self_improvement", "tool_creation_allowed_capabilities", '["text:slugify"]'
    )


async def test_service_absent_gap_dispatches_ops_signal_not_authoring(
    settings: SettingsService,
) -> None:
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
    svc = _service(settings, generator=generator, dispatcher=dispatcher)
    await _enable(settings)
    await svc.record_gap(_CAP, occurred_at=_NOW, kind=GapKind.SERVICE_ABSENT)
    await svc.record_gap(
        _CAP, occurred_at=_NOW + timedelta(minutes=1), kind=GapKind.SERVICE_ABSENT
    )

    proposals = await svc.run_cycle(now=_NOW + timedelta(minutes=2))

    assert proposals == ()
    generator.author.assert_not_awaited()
    dispatcher.dispatch.assert_awaited_once()
    note = dispatcher.dispatch.await_args.args[0]
    assert isinstance(note, Notification)
    assert note.category is NotificationCategory.SYSTEM
    assert note.severity is NotificationSeverity.WARNING
    assert "text:slugify" in note.body


async def test_missing_tool_gap_still_reaches_authoring(
    settings: SettingsService,
) -> None:
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    generator.author.side_effect = ToolAuthoringError("stop after gate")
    dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
    svc = _service(settings, generator=generator, dispatcher=dispatcher)
    await _enable(settings)
    await svc.record_gap(_CAP, occurred_at=_NOW, kind=GapKind.MISSING_TOOL)
    await svc.record_gap(
        _CAP, occurred_at=_NOW + timedelta(minutes=1), kind=GapKind.MISSING_TOOL
    )

    proposals = await svc.run_cycle(now=_NOW + timedelta(minutes=2))

    assert proposals == ()
    generator.author.assert_awaited_once()
    dispatcher.dispatch.assert_not_called()


async def test_service_absent_alert_deduped_per_capability(
    settings: SettingsService,
) -> None:
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
    svc = _service(settings, generator=generator, dispatcher=dispatcher)
    gap = CapabilityGap(
        signature=_CAP,
        kind=GapKind.SERVICE_ABSENT,
        occurrences=2,
        first_seen=_NOW,
        last_seen=_NOW,
    )

    await svc._signal_service_absent(gap)
    await svc._signal_service_absent(gap)

    # The same unresolved capability alerts once, not every cycle.
    dispatcher.dispatch.assert_awaited_once()


async def test_service_absent_without_dispatcher_is_safe(
    settings: SettingsService,
) -> None:
    generator = AsyncMock(spec=ToolBlueprintGenerator)
    svc = _service(settings, generator=generator, dispatcher=None)
    await _enable(settings)
    await svc.record_gap(_CAP, occurred_at=_NOW, kind=GapKind.SERVICE_ABSENT)
    await svc.record_gap(
        _CAP, occurred_at=_NOW + timedelta(minutes=1), kind=GapKind.SERVICE_ABSENT
    )

    proposals = await svc.run_cycle(now=_NOW + timedelta(minutes=2))

    assert proposals == ()
    generator.author.assert_not_awaited()
