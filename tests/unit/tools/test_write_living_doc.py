"""Unit tests for ``WriteLivingDocTool`` task-id auto-stamping.

The deliverable-receipt builder resolves a task's deliverable by
scanning ``related_task_ids``. Agents do not reliably self-link, so the
tool stamps the producing task (bound on the execution identity) into
the persisted links. These tests pin that behaviour: stamp inside a
run scope, pass through outside one, and never duplicate.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import DocType
from synthorg.core.execution_identity import (
    ExecutionIdentity,
    execution_identity_scope,
)
from synthorg.docs_engine.models import DocMetadata
from synthorg.docs_engine.service import DocsService
from synthorg.tools.docs.write_living_doc import WriteLivingDocTool
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
_TASK_ID = "task-1"


def _metadata() -> DocMetadata:
    return DocMetadata(
        project_id="proj-1",
        slug="quarterly-report",
        doc_type=DocType.DELIVERABLE,
        title="Quarterly Report",
        head_commit_sha="0" * 40,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _tool() -> tuple[WriteLivingDocTool, AsyncMock]:
    docs_service = mock_of[DocsService]()
    write_doc = AsyncMock(spec=DocsService.write_doc, return_value=_metadata())
    docs_service.write_doc = write_doc
    tool = WriteLivingDocTool(
        docs_service=docs_service,
        project_id="proj-1",
        author_agent_id="bob",
    )
    return tool, write_doc


def _related(write_doc: AsyncMock) -> tuple[str, ...]:
    """Return the ``related_task_ids`` the tool passed to ``write_doc``."""
    assert write_doc.await_args is not None
    related = write_doc.await_args.kwargs["related_task_ids"]
    assert isinstance(related, tuple)
    return related


def _arguments(related_task_ids: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "title": "Quarterly Report",
        "doc_type": "deliverable",
        "body": [{"block_kind": "prose", "text": "The deliverable body."}],
        "related_task_ids": list(related_task_ids),
    }


async def test_stamps_producing_task_inside_run_scope() -> None:
    tool, write_doc = _tool()
    identity = ExecutionIdentity(
        execution_id="exec-1",
        task_id=_TASK_ID,
        project_id="proj-1",
    )
    with execution_identity_scope(identity):
        result = await tool.execute(arguments=_arguments())
    assert result.is_error is False
    assert _related(write_doc) == (_TASK_ID,)


async def test_passes_through_outside_run_scope() -> None:
    tool, write_doc = _tool()
    result = await tool.execute(arguments=_arguments(("other-task",)))
    assert result.is_error is False
    assert _related(write_doc) == ("other-task",)


async def test_does_not_duplicate_already_linked_task() -> None:
    tool, write_doc = _tool()
    identity = ExecutionIdentity(execution_id="exec-1", task_id=_TASK_ID)
    with execution_identity_scope(identity):
        await tool.execute(arguments=_arguments((_TASK_ID,)))
    assert _related(write_doc) == (_TASK_ID,)


async def test_appends_task_preserving_agent_links() -> None:
    tool, write_doc = _tool()
    identity = ExecutionIdentity(execution_id="exec-1", task_id=_TASK_ID)
    with execution_identity_scope(identity):
        await tool.execute(arguments=_arguments(("agent-link",)))
    assert _related(write_doc) == ("agent-link", _TASK_ID)
