"""Approve-to-live consumer: an approved tool goes live.

The consumer rehydrates a persisted blueprint from an APPROVED
``proposal:tool_creation`` item, atomically claims the grant, and applies it
through the service. These cover the happy path, the skip paths (no blueprint
id, blueprint absent, claim lost), and that a lost claim never applies.
"""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ApplyResult
from synthorg.meta.toolsmith.approval_consumer import ToolApprovalConsumer
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.service import ToolsmithService
from synthorg.persistence.tool_blueprint_protocol import DynamicToolRepository
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def _asmock(method: object) -> AsyncMock:
    """Narrow a ``mock_of`` autospec method to ``AsyncMock`` for assertions.

    ``mock_of[T](...)`` is typed ``Any`` in isolation but resolves to the
    concrete ``T`` under full-project type-checking, so its methods lose the
    mock-assertion surface; this cast restores it without an explicit ``Any``.
    """
    return cast(AsyncMock, method)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id=NotBlankStr("bp-1"),
        name=NotBlankStr("synthorg_textkit_slugify"),
        description=NotBlankStr("Slugify text."),
        capability=NotBlankStr("textkit:slugify"),
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body=NotBlankStr("print('{}')"),
        action_type=NotBlankStr("code:read"),
        created_at=_NOW - timedelta(minutes=1),
    )


def _approval(*, blueprint_id: str | None = "bp-1") -> ApprovalItem:
    metadata: dict[str, str] = {"proposal_id": "prop-1"}
    if blueprint_id is not None:
        metadata["blueprint_id"] = blueprint_id
    return ApprovalItem(
        id=UUID(str(as_uuid("approval-1"))),
        action_type=NotBlankStr("proposal:tool_creation"),
        title=NotBlankStr("Author tool for textkit:slugify"),
        description=NotBlankStr("An authored tool."),
        requested_by=NotBlankStr("meta_improvement_service"),
        risk_level=ApprovalRiskLevel.HIGH,
        created_at=_NOW,
        metadata=metadata,
    )


async def test_approved_tool_is_applied_live() -> None:
    blueprint = _blueprint()
    item = _approval()
    service = mock_of[ToolsmithService](
        apply=AsyncMock(return_value=ApplyResult(success=True, changes_applied=1))
    )
    store = mock_of[ApprovalStoreProtocol](
        list_items=AsyncMock(return_value=(item,)),
        consume_if_approved=AsyncMock(return_value=item),
    )
    consumer = ToolApprovalConsumer(
        service=service,
        blueprint_repo=mock_of[DynamicToolRepository](
            get=AsyncMock(return_value=blueprint)
        ),
        approval_store=store,
    )

    applied = await consumer.consume()

    assert applied == 1
    _asmock(service.apply).assert_awaited_once()
    await_args = _asmock(service.apply).await_args
    assert await_args is not None
    proposal = await_args.args[0]
    assert proposal.tool_changes == (blueprint,)


async def test_item_without_blueprint_id_is_skipped() -> None:
    item = _approval(blueprint_id=None)
    service = mock_of[ToolsmithService](
        apply=AsyncMock(return_value=ApplyResult(success=True, changes_applied=1))
    )
    store = mock_of[ApprovalStoreProtocol](
        list_items=AsyncMock(return_value=(item,)),
        consume_if_approved=AsyncMock(return_value=item),
    )
    consumer = ToolApprovalConsumer(
        service=service,
        blueprint_repo=mock_of[DynamicToolRepository](
            get=AsyncMock(return_value=_blueprint())
        ),
        approval_store=store,
    )

    assert await consumer.consume() == 0
    _asmock(service.apply).assert_not_called()
    _asmock(store.consume_if_approved).assert_not_called()


async def test_missing_blueprint_is_skipped() -> None:
    item = _approval()
    service = mock_of[ToolsmithService](
        apply=AsyncMock(return_value=ApplyResult(success=True, changes_applied=1))
    )
    consumer = ToolApprovalConsumer(
        service=service,
        blueprint_repo=mock_of[DynamicToolRepository](get=AsyncMock(return_value=None)),
        approval_store=mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(item,)),
            consume_if_approved=AsyncMock(return_value=item),
        ),
    )

    assert await consumer.consume() == 0
    _asmock(service.apply).assert_not_called()


async def test_lost_claim_never_applies() -> None:
    item = _approval()
    service = mock_of[ToolsmithService](
        apply=AsyncMock(return_value=ApplyResult(success=True, changes_applied=1))
    )
    consumer = ToolApprovalConsumer(
        service=service,
        blueprint_repo=mock_of[DynamicToolRepository](
            get=AsyncMock(return_value=_blueprint())
        ),
        # consume_if_approved returns None: already consumed or a concurrent claim.
        approval_store=mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(item,)),
            consume_if_approved=AsyncMock(return_value=None),
        ),
    )

    assert await consumer.consume() == 0
    _asmock(service.apply).assert_not_called()


async def test_apply_rejection_counts_as_not_applied() -> None:
    item = _approval()
    service = mock_of[ToolsmithService](
        apply=AsyncMock(
            return_value=ApplyResult(
                success=False,
                error_message=NotBlankStr("gate rejected"),
                changes_applied=0,
            )
        )
    )
    consumer = ToolApprovalConsumer(
        service=service,
        blueprint_repo=mock_of[DynamicToolRepository](
            get=AsyncMock(return_value=_blueprint())
        ),
        approval_store=mock_of[ApprovalStoreProtocol](
            list_items=AsyncMock(return_value=(item,)),
            consume_if_approved=AsyncMock(return_value=item),
        ),
    )

    assert await consumer.consume() == 0
    # The claim still happened (the grant is one-shot), but nothing went live.
    _asmock(service.apply).assert_awaited_once()
