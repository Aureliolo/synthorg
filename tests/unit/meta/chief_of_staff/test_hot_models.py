"""Hot-reload coverage for the per-feature Chief-of-Staff models.

``propose_model`` / ``routing_model`` / ``narrative_model`` are each read
live per call through the shared ``resolve_model_with_fallback`` seam, so an
operator can retarget any of them without a restart. (``chat_model`` is
covered in ``test_chat_hot_model.py``; the shared seam itself in
``tests/unit/settings/test_kill_switch.py``.)
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.config.schema import RootConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.chief_of_staff.narrative.models import ReducedRun, RunMetric
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from synthorg.meta.chief_of_staff.routing import LlmConcernRouter
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import as_uuid, mock_of, sid
from tests._shared.scripted_provider import ScriptedProvider
from tests.unit.api.fakes import FakePersistenceBackend
from tests.unit.meta.chief_of_staff.propose_fakes import (
    build_proposer,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.unit


def _bound(model_id: str) -> str:
    """Serialize a bound ``{provider, model_id}`` MODEL_REF for a settings write."""
    return serialize_model_ref(ModelRef(provider="example-provider", model_id=model_id))


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _resolver(settings: SettingsService) -> ConfigResolver:
    return ConfigResolver(
        settings_service=settings, config=RootConfig(company_name="test")
    )


def _response(content: str) -> CompletionResponse:
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.001),
        model="recorded-elsewhere",
    )


async def test_propose_model_read_live(settings: SettingsService) -> None:
    """``propose_model`` resolves live per turn with a baked fallback."""
    proposer, *_ = build_proposer(
        provider=ScriptedProvider(responses=[]),
        config=ChiefOfStaffConfig(propose_enabled=True, propose_model="baked-prop-001"),
        config_resolver=_resolver(settings),
    )

    assert await proposer._resolve_propose_model() == "baked-prop-001"

    await settings.set("chief_of_staff", "propose_model", _bound("live-prop-001"))
    assert await proposer._resolve_propose_model() == "live-prop-001"


async def test_routing_model_read_live(settings: SettingsService) -> None:
    """``routing_model`` is the model passed to the classifier call live."""
    provider = mock_of[CompletionProvider](
        complete=AsyncMock(
            return_value=_response(
                '{"topic": "budget", "role": "CEO", "confidence": 0.9}'
            )
        )
    )
    registry = await build_registry(make_identity(name="Dana", role="CEO"))
    router = LlmConcernRouter(
        provider=provider,
        model=NotBlankStr("baked-route-001"),
        agent_registry=registry,
        confidence_floor=0.6,
        default_role=NotBlankStr("CEO"),
        temperature=0.0,
        max_tokens=200,
        timeout_seconds=120.0,
        config_resolver=_resolver(settings),
    )
    turns = (
        ConversationTurn(
            id=as_uuid("turn-1"),
            conversation_id=sid("conv-1"),
            sequence=0,
            role=ConversationRole.USER,
            content=NotBlankStr("How much runway is left?"),
            created_at=datetime(2026, 5, 19, 9, 0, 0, tzinfo=UTC),
        ),
    )

    await router.route(turns)
    assert provider.complete.await_args.args[1] == "baked-route-001"

    await settings.set("chief_of_staff", "routing_model", _bound("live-route-001"))
    await router.route(turns)
    assert provider.complete.await_args.args[1] == "live-route-001"


def _reduced() -> ReducedRun:
    return ReducedRun(
        project_id=NotBlankStr("proj-1"),
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        brief_title=NotBlankStr("Ship checkout"),
        final_status=TaskStatus.COMPLETED,
        metrics=(RunMetric(name="Turns", value="12"),),
        decisions=(),
        outcomes=("Final status: completed",),
    )


async def test_narrative_model_read_live(settings: SettingsService) -> None:
    """``narrative_model`` is the model passed to the prose call live."""
    provider = mock_of[CompletionProvider](
        complete=AsyncMock(return_value=_response('{"summary": "Shipped."}'))
    )
    synth = NarrativeSynthesiser(
        provider=provider,
        config=ChiefOfStaffConfig(narrative_model="baked-narr-001"),
        config_resolver=_resolver(settings),
    )

    await synth.write_prose(_reduced())
    assert provider.complete.await_args.args[1] == "baked-narr-001"

    await settings.set("chief_of_staff", "narrative_model", _bound("live-narr-001"))
    await synth.write_prose(_reduced())
    assert provider.complete.await_args.args[1] == "live-narr-001"
