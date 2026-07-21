# module-kind: tests
"""End-to-end proof that a completed objective writes a retrospective.

Drives the whole SHIP-time consuming tail through the public
``schedule`` + ``drain`` path: the lead runs a real distillation session (a
real ``ReactLoop`` over a scripted provider that calls ``submit_retrospective``),
and the distilled learnings land in org and agent memory. Only the LLM is a
deterministic stand-in.
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.retro_capture import ShipRetroCaptureService
from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.models import MemoryQuery
from synthorg.memory.org.protocol import OrgMemoryBackend
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_text_response,
)

pytestmark = pytest.mark.integration

_LEAD_ID = as_uuid("retro-lead")


def _lead() -> AgentIdentity:
    return AgentIdentity(
        id=_LEAD_ID,
        name="Retro Lead",
        role="Engineering Manager",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _project() -> Project:
    return Project(
        id=as_uuid("retro-proj"),
        name=NotBlankStr("Checkout Hardening"),
        team=(NotBlankStr(str(_LEAD_ID)),),
        lead=NotBlankStr(str(_LEAD_ID)),
    )


def _plan() -> Plan:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    return Plan(
        id=as_uuid("retro-plan"),
        project=NotBlankStr(sid("retro-proj")),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Harden checkout"),
        parent_task_id=NotBlankStr(sid("parent")),
        items=(
            PlanItem(
                id=NotBlankStr(sid("i1")),
                title=NotBlankStr("Add retries"),
                description=NotBlankStr("retry logic"),
                acceptance_criteria=(NotBlankStr("retries covered"),),
                kind=PlanItemKind.WORK,
            ),
        ),
        status=PlanStatus.COMPLETED,
        created_at=now,
        updated_at=now,
    )


def _retro_args() -> dict[str, JsonValue]:
    return {
        "summary": "Shipped the checkout hardening cleanly.",
        "org_learnings": [
            {
                "content": "Prefer idempotent retries on payment calls.",
                "kind": "convention",
            },
        ],
        "agent_learnings": [
            {"agent_id": str(_LEAD_ID), "content": "Pull the load test forward."},
        ],
    }


async def test_completed_objective_writes_org_and_agent_learnings() -> None:
    agent_memory = InMemoryBackend()
    await agent_memory.connect()
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()),
        write=AsyncMock(return_value=NotBlankStr("fact-1")),
    )
    registry = mock_of[AgentRegistryService](get=AsyncMock(return_value=_lead()))
    provider = ScriptedProvider(
        [
            build_tool_call_response("submit_retrospective", _retro_args()),
            make_text_response("Retrospective submitted."),
        ]
    )
    service = ShipRetroCaptureService(
        agent_registry=registry,
        memory_backend=agent_memory,
        org_backend=org_backend,
        provider_selector=lambda _identity: provider,
        default_provider=None,
        config_resolver=None,
        clock=FakeClock(),
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    # Org memory received the reusable convention learning.
    org_backend.write.assert_awaited_once()
    org_request = org_backend.write.await_args.args[0]
    assert org_request.category is OrgFactCategory.CONVENTION
    assert "idempotent retries" in org_request.content

    # The lead's own memory carries their per-agent learning.
    stored = await agent_memory.retrieve(
        NotBlankStr(str(_LEAD_ID)), MemoryQuery(text="load test")
    )
    assert any("load test" in entry.content for entry in stored)


async def test_capture_skipped_when_already_captured() -> None:
    """An objective already carrying a retrospective is not re-distilled."""
    from synthorg.engine.initiative.retro_models import retro_object_tag
    from synthorg.memory.org.models import OrgFact, OrgFactAuthor

    existing = OrgFact(
        content=NotBlankStr("Prior learning."),
        category=OrgFactCategory.CONVENTION,
        tags=(retro_object_tag(str(as_uuid("retro-proj"))),),
        author=OrgFactAuthor(is_human=True),
        created_at=datetime(2026, 7, 19, tzinfo=UTC),
    )
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=(existing,)),
        write=AsyncMock(),
    )
    registry = mock_of[AgentRegistryService](get=AsyncMock(return_value=_lead()))
    provider = ScriptedProvider([make_text_response("unused")])
    service = ShipRetroCaptureService(
        agent_registry=registry,
        memory_backend=InMemoryBackend(),
        org_backend=org_backend,
        provider_selector=lambda _identity: provider,
        default_provider=None,
        config_resolver=None,
        clock=FakeClock(),
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    # No session ran and nothing was written.
    assert provider.call_count == 0
    org_backend.write.assert_not_awaited()
