"""Approve-to-live consumer: an approved tool goes live.

The consumer rehydrates a persisted blueprint from an APPROVED
``proposal:tool_creation`` item, atomically claims the grant, and applies it
through the service. These cover the happy path, the skip paths (no blueprint
id, blueprint absent, claim lost), and that a lost claim never applies.
"""

from datetime import UTC, datetime, timedelta
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


def _store(
    *, items: tuple[ApprovalItem, ...], claim: ApprovalItem | None
) -> ApprovalStoreProtocol:
    return mock_of[ApprovalStoreProtocol](
        list_items=AsyncMock(return_value=items),
        consume_if_approved=AsyncMock(return_value=claim),
    )


def _repo(blueprint: ToolBlueprint | None) -> DynamicToolRepository:
    return mock_of[DynamicToolRepository](get=AsyncMock(return_value=blueprint))


def _service(*, apply_result: ApplyResult) -> ToolsmithService:
    return mock_of[ToolsmithService](apply=AsyncMock(return_value=apply_result))


async def test_approved_tool_is_applied_live() -> None:
    blueprint = _blueprint()
    item = _approval()
    service = _service(apply_result=ApplyResult(success=True, changes_applied=1))
    store = _store(items=(item,), claim=item)
    consumer = ToolApprovalConsumer(
        service=service, blueprint_repo=_repo(blueprint), approval_store=store
    )

    applied = await consumer.consume()

    assert applied == 1
    service.apply.assert_awaited_once()
    proposal = service.apply.await_args.args[0]
    assert proposal.tool_changes == (blueprint,)


async def test_item_without_blueprint_id_is_skipped() -> None:
    item = _approval(blueprint_id=None)
    service = _service(apply_result=ApplyResult(success=True, changes_applied=1))
    store = _store(items=(item,), claim=item)
    consumer = ToolApprovalConsumer(
        service=service, blueprint_repo=_repo(_blueprint()), approval_store=store
    )

    assert await consumer.consume() == 0
    service.apply.assert_not_called()
    store.consume_if_approved.assert_not_called()


async def test_missing_blueprint_is_skipped() -> None:
    item = _approval()
    service = _service(apply_result=ApplyResult(success=True, changes_applied=1))
    store = _store(items=(item,), claim=item)
    consumer = ToolApprovalConsumer(
        service=service, blueprint_repo=_repo(None), approval_store=store
    )

    assert await consumer.consume() == 0
    service.apply.assert_not_called()


async def test_lost_claim_never_applies() -> None:
    item = _approval()
    service = _service(apply_result=ApplyResult(success=True, changes_applied=1))
    # consume_if_approved returns None: already consumed or a concurrent claim.
    store = _store(items=(item,), claim=None)
    consumer = ToolApprovalConsumer(
        service=service, blueprint_repo=_repo(_blueprint()), approval_store=store
    )

    assert await consumer.consume() == 0
    service.apply.assert_not_called()


async def test_apply_rejection_counts_as_not_applied() -> None:
    item = _approval()
    service = _service(
        apply_result=ApplyResult(
            success=False,
            error_message=NotBlankStr("gate rejected"),
            changes_applied=0,
        )
    )
    store = _store(items=(item,), claim=item)
    consumer = ToolApprovalConsumer(
        service=service, blueprint_repo=_repo(_blueprint()), approval_store=store
    )

    assert await consumer.consume() == 0
    # The claim still happened (the grant is one-shot), but nothing went live.
    service.apply.assert_awaited_once()
