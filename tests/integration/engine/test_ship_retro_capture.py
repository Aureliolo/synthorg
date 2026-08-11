# module-kind: tests
"""End-to-end proof that a completed objective writes a retrospective.

Drives the whole SHIP-time consuming tail through the public
``schedule`` + ``drain`` path: the lead runs a real distillation session (a
real ``ReactLoop`` over a scripted provider that calls ``submit_retrospective``),
and the distilled learnings land in org and agent memory. Only the LLM is a
deterministic stand-in.
"""

import asyncio
from datetime import UTC, date, datetime
from typing import override
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.retro_capture import ShipRetroCaptureService
from synthorg.engine.initiative.retro_models import retro_object_tag
from synthorg.hr.registry import AgentRegistryService
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.models import MemoryQuery
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider, ProviderSelector
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_text_response,
)

pytestmark = pytest.mark.integration

_LEAD_ID = as_uuid("retro-lead")


def _service(
    *,
    org_backend: OrgMemoryBackend,
    provider: CompletionProvider,
    memory_backend: MemoryBackend | None = None,
    registry: AgentRegistryService | None = None,
    config_resolver: ConfigResolver | None = None,
    provider_selector: ProviderSelector | None = None,
) -> ShipRetroCaptureService:
    """Build a capture service with sensible test defaults."""
    return ShipRetroCaptureService(
        agent_registry=registry
        or mock_of[AgentRegistryService](get=AsyncMock(return_value=_lead())),
        memory_backend=memory_backend or InMemoryBackend(),
        org_backend=org_backend,
        provider_selector=provider_selector or (lambda _identity: provider),
        config_resolver=config_resolver,
        clock=FakeClock(),
    )


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
                expected_artifacts=(NotBlankStr("src/retries.py"),),
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
        config_resolver=None,
        clock=FakeClock(),
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    # No session ran and nothing was written.
    assert provider.call_count == 0
    org_backend.write.assert_not_awaited()


def _resolver(
    *,
    enabled: bool = True,
    max_turns: int = 8,
    cost_ceiling: float = 1.0,
    timeout_seconds: float = 30.0,
) -> ConfigResolver:
    """A config resolver returning the given live retro settings."""
    floats = {
        "retro_session_cost_ceiling": cost_ceiling,
        "retro_session_timeout_seconds": timeout_seconds,
    }

    async def _get_float(_ns: str, key: str) -> float:
        return floats[key]

    async def _get_int(_ns: str, _key: str) -> int:
        return max_turns

    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_bool=AsyncMock(return_value=enabled),
        get_int=AsyncMock(side_effect=_get_int),
        get_float=AsyncMock(side_effect=_get_float),
    )
    return resolver


async def test_capture_skipped_when_disabled() -> None:
    """The kill switch stops a session running and any write."""
    org_backend = mock_of[OrgMemoryBackend](query=AsyncMock(), write=AsyncMock())
    provider = ScriptedProvider([make_text_response("unused")])
    service = _service(
        org_backend=org_backend,
        provider=provider,
        config_resolver=_resolver(enabled=False),
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    assert provider.call_count == 0
    org_backend.query.assert_not_awaited()
    org_backend.write.assert_not_awaited()


async def test_capture_skipped_when_no_lead() -> None:
    """A completed objective with no resolvable author writes nothing."""
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()), write=AsyncMock()
    )
    provider = ScriptedProvider([make_text_response("unused")])
    registry = mock_of[AgentRegistryService](
        get=AsyncMock(return_value=None),
        get_by_ids=AsyncMock(return_value={}),
    )
    leadless = Project(
        id=as_uuid("retro-proj"),
        name=NotBlankStr("Checkout Hardening"),
        team=(),
    )
    service = _service(org_backend=org_backend, provider=provider, registry=registry)

    service.schedule(plan=_plan(), project=leadless)
    await service.drain(timeout_sec=30.0)

    assert provider.call_count == 0
    org_backend.write.assert_not_awaited()


async def test_capture_skipped_when_no_provider() -> None:
    """An unresolvable lead provider with no default skips capture."""
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()), write=AsyncMock()
    )

    def _select(_identity: AgentIdentity) -> CompletionProvider:
        msg = "no provider"
        raise DriverNotRegisteredError(msg)

    service = _service(
        org_backend=org_backend,
        provider=ScriptedProvider([make_text_response("unused")]),
        provider_selector=_select,
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    org_backend.write.assert_not_awaited()


async def test_capture_writes_nothing_when_no_draft_is_submitted() -> None:
    """A session that never calls submit_retrospective persists nothing."""
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()), write=AsyncMock()
    )
    # The provider only ever emits text, so the bounded loop ends without a draft.
    provider = ScriptedProvider([make_text_response("no tool call")] * 12)
    service = _service(
        org_backend=org_backend,
        provider=provider,
        config_resolver=_resolver(max_turns=2),
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    org_backend.write.assert_not_awaited()


async def test_concurrent_schedule_for_one_project_captures_once() -> None:
    """A second schedule for a project already in flight is a no-op."""
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()),
        write=AsyncMock(return_value=NotBlankStr("fact-1")),
    )
    provider = ScriptedProvider(
        [
            build_tool_call_response("submit_retrospective", _retro_args()),
            make_text_response("submitted"),
        ]
    )
    service = _service(org_backend=org_backend, provider=provider)

    service.schedule(plan=_plan(), project=_project())
    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    # Only one session ran; the duplicate schedule was collapsed.
    org_backend.write.assert_awaited_once()


async def test_distiller_reads_live_turn_and_cost_settings() -> None:
    """The session config is resolved from live settings, not hardcoded."""
    org_backend = mock_of[OrgMemoryBackend](query=AsyncMock(), write=AsyncMock())
    service = _service(
        org_backend=org_backend,
        provider=ScriptedProvider([make_text_response("unused")]),
        config_resolver=_resolver(max_turns=3, cost_ceiling=0.5),
    )

    distiller = await service._distiller()

    assert distiller._config.max_turns == 3
    assert distiller._config.ceilings.cost_ceiling == 0.5
    # The token bound comes from the same resolution, so a config carrying
    # only the money one would leave the session unbounded on a flat-rate
    # provider, where cost never rises.
    assert distiller._config.ceilings.token_ceiling > 0


async def test_total_write_failure_does_not_raise() -> None:
    """A total write-side outage is swallowed, not propagated into the loop."""
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()),
        write=AsyncMock(side_effect=RuntimeError("store down")),
    )
    agent_memory = mock_of[MemoryBackend](
        store=AsyncMock(side_effect=RuntimeError("store down"))
    )
    provider = ScriptedProvider(
        [
            build_tool_call_response("submit_retrospective", _retro_args()),
            make_text_response("submitted"),
        ]
    )
    service = _service(
        org_backend=org_backend, provider=provider, memory_backend=agent_memory
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    # The write was attempted and its failure did not escape the capture task.
    org_backend.write.assert_awaited()


class _HangingProvider(ScriptedProvider):
    """A provider whose completion blocks until cancelled, to force a timeout."""

    def __init__(self) -> None:
        super().__init__([make_text_response("never returned")])
        self._gate = asyncio.Event()

    @override
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        await self._gate.wait()
        msg = "gate never opens"
        raise AssertionError(msg)


async def test_capture_times_out_when_distillation_hangs() -> None:
    """A hung distillation is bounded by the wall-clock ceiling and writes nothing."""
    org_backend = mock_of[OrgMemoryBackend](
        query=AsyncMock(return_value=()), write=AsyncMock()
    )
    service = _service(
        org_backend=org_backend,
        provider=_HangingProvider(),
        config_resolver=_resolver(timeout_seconds=0.05),
    )

    service.schedule(plan=_plan(), project=_project())
    await service.drain(timeout_sec=30.0)

    org_backend.write.assert_not_awaited()
