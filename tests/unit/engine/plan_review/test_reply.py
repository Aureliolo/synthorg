# module-kind: tests
"""Unit tests for the conversational plan-item reply service."""

import asyncio
from datetime import UTC, date, datetime
from typing import override

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.engine.plan_review.reply import (
    AgentReply,
    LlmPlanItemReplyService,
    build_plan_item_reply_service,
)
from synthorg.hr.enums import AgentStatus
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from tests._shared import as_uuid
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _identity(name: str, role: str) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(name),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr("executive"),
        personality=PersonalityConfig(
            traits=(NotBlankStr("analytical"),),
            communication_style=NotBlankStr("concise"),
        ),
        model=ModelConfig(
            provider=NotBlankStr("test-provider"),
            model_id=NotBlankStr("test-model-001"),
            temperature=0.7,
            max_tokens=4096,
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


_CFO = _identity("Casey", "CFO")
_ROSTER: tuple[AgentIdentity, ...] = (_CFO,)


def _item(*, owner: str | None) -> PlanItem:
    return PlanItem(
        id=str(as_uuid("item-1")),
        title=NotBlankStr("Ship the billing migration"),
        description=NotBlankStr("Move billing to the new ledger."),
        owner=NotBlankStr(owner) if owner is not None else None,
        acceptance_criteria=(NotBlankStr("Ledger reconciles to the cent"),),
        expected_artifacts=(NotBlankStr("src/billing/ledger.py"),),
    )


def _plan(*, owner: str | None) -> Plan:
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr("Growth"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Reduce billing risk"),
        parent_task_id=NotBlankStr("task-1"),
        items=(_item(owner=owner),),
        created_at=_T0,
        updated_at=_T0,
    )


def _scripted(text: str) -> ScriptedProvider:
    return ScriptedProvider(responses=[make_text_response(text)])


def _service(
    provider: ScriptedProvider, *, timeout_seconds: float = 120.0
) -> LlmPlanItemReplyService:
    return LlmPlanItemReplyService(
        provider=provider,
        model=NotBlankStr("test-model-001"),
        temperature=0.3,
        max_tokens=600,
        timeout_seconds=timeout_seconds,
    )


class _HangingProvider(ScriptedProvider):
    """Provider whose ``complete`` never returns, to exercise the timeout."""

    def __init__(self) -> None:
        super().__init__(responses=[])

    @override
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        del messages, model, tools, config
        await asyncio.Event().wait()
        raise AssertionError  # unreachable


async def _reply(
    service: LlmPlanItemReplyService, *, owner: str | None
) -> AgentReply | None:
    plan = _plan(owner=owner)
    return await service.reply(
        plan=plan,
        item=plan.items[0],
        comment_body="Why this ledger over the incumbent?",
        active=_ROSTER,
    )


class TestLlmPlanItemReplyService:
    async def test_owner_role_answers_with_attribution(self) -> None:
        reply = await _reply(
            _service(_scripted("The new ledger nets out FX.")), owner="CFO"
        )
        assert reply is not None
        assert reply.author == "Casey"
        assert reply.author_agent_id == str(as_uuid("Casey"))
        assert reply.body == "The new ledger nets out FX."

    async def test_unowned_item_falls_back_to_chief_of_staff(self) -> None:
        reply = await _reply(_service(_scripted("Here is the rationale.")), owner=None)
        assert reply is not None
        assert reply.author == "Chief of Staff"
        assert reply.author_agent_id == "chief-of-staff"

    async def test_unresolved_owner_falls_back_to_chief_of_staff(self) -> None:
        # "Legal" is not on the roster, so no active agent holds the role.
        reply = await _reply(_service(_scripted("A note.")), owner="Legal")
        assert reply is not None
        assert reply.author == "Chief of Staff"

    async def test_empty_reply_yields_none(self) -> None:
        assert await _reply(_service(_scripted("   ")), owner="CFO") is None

    async def test_timeout_yields_none(self) -> None:
        service = _service(_HangingProvider(), timeout_seconds=0.05)
        assert await _reply(service, owner="CFO") is None


class TestBuildPlanItemReplyService:
    def test_unconfigured_model_returns_none(self) -> None:
        assert (
            build_plan_item_reply_service(
                reply_model="",
                temperature=0.3,
                max_tokens=600,
                timeout_seconds=120.0,
                provider_registry=ProviderRegistry(drivers={}),
            )
            is None
        )

    def test_provider_less_ref_returns_none(self) -> None:
        ref = serialize_model_ref(ModelRef(provider="", model_id="example-small-001"))
        assert (
            build_plan_item_reply_service(
                reply_model=ref,
                temperature=0.3,
                max_tokens=600,
                timeout_seconds=120.0,
                provider_registry=ProviderRegistry(drivers={}),
            )
            is None
        )

    def test_unregistered_provider_returns_none(self) -> None:
        ref = serialize_model_ref(
            ModelRef(provider="ghost", model_id="example-small-001")
        )
        assert (
            build_plan_item_reply_service(
                reply_model=ref,
                temperature=0.3,
                max_tokens=600,
                timeout_seconds=120.0,
                provider_registry=ProviderRegistry(drivers={}),
            )
            is None
        )
