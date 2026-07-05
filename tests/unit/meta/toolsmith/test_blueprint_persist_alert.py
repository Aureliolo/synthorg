"""A failed durable blueprint save alerts the operator (best-effort).

The guard chain registers an approval item referencing the blueprint's id
before the durable save, so a failed save leaves that approval unfulfillable.
An operator alert lets the operator re-propose rather than discovering it only
by log-grep, matching the other toolsmith failure paths.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.applier import ToolCreationApplier
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.meta.toolsmith.gap_store import RingBufferCapabilityGapStore
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.protocol import ToolBlueprintGenerator
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import Notification, NotificationSeverity
from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id=NotBlankStr("bp-1"),
        name=NotBlankStr("synthorg_textkit_slugify"),
        description=NotBlankStr("Slugify text."),
        capability=NotBlankStr("textkit:slugify"),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        script_body=NotBlankStr("print('x')"),
        action_type=NotBlankStr("code:read"),
        created_at=_NOW - timedelta(minutes=1),
    )


def _service(
    *,
    blueprint_repo: DynamicToolRepository,
    dispatcher: NotificationDispatcher | None,
) -> ToolsmithService:
    return ToolsmithService(
        config=ToolsmithConfig(
            enabled=True, allowed_capabilities=(NotBlankStr("textkit:slugify"),)
        ),
        gap_store=RingBufferCapabilityGapStore(max_observations=8),
        generator=AsyncMock(spec=ToolBlueprintGenerator),
        applier=AsyncMock(spec=ToolCreationApplier),
        guards=(),
        blueprint_repo=blueprint_repo,
        notification_dispatcher=dispatcher,
    )


async def test_persist_failure_alerts_the_operator() -> None:
    dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
    repo = mock_of[DynamicToolRepository](
        save=AsyncMock(side_effect=RuntimeError("db down"))
    )
    svc = _service(blueprint_repo=repo, dispatcher=dispatcher)

    await svc._persist_pending_blueprint(_blueprint())

    dispatcher.dispatch.assert_awaited_once()
    note = dispatcher.dispatch.await_args.args[0]
    assert isinstance(note, Notification)
    assert note.severity is NotificationSeverity.WARNING
    assert "textkit:slugify" in note.body


async def test_persist_failure_without_dispatcher_is_safe() -> None:
    repo = mock_of[DynamicToolRepository](
        save=AsyncMock(side_effect=RuntimeError("db down"))
    )
    svc = _service(blueprint_repo=repo, dispatcher=None)

    # No dispatcher wired: the failure is logged and swallowed, never raised.
    await svc._persist_pending_blueprint(_blueprint())
