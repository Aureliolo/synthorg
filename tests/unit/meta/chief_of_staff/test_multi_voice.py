# module-kind: tests
"""Unit tests for multi-voice chime-in selection."""

import asyncio
from datetime import date
from typing import override

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.meta.chief_of_staff._multi_voice import (
    ChimeIn,
    LlmMultiVoiceRouter,
    _senior_per_role,
    build_multi_voice_router,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
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
_CTO = _identity("Devi", "CTO")
_ROSTER: tuple[AgentIdentity, ...] = (_CFO, _CTO)


def _voices_json(*rows: tuple[str, str, float]) -> str:
    entries = ", ".join(
        f'{{"role": "{role}", "content": "{content}", "confidence": {conf}}}'
        for role, content, conf in rows
    )
    return f'{{"voices": [{entries}]}}'


def _router(
    *,
    provider: ScriptedProvider,
    floor: float = 0.7,
    max_speakers: int = 2,
    timeout_seconds: float = 120.0,
) -> LlmMultiVoiceRouter:
    return LlmMultiVoiceRouter(
        provider=provider,
        model=NotBlankStr("test-model-001"),
        confidence_floor=floor,
        max_speakers=max_speakers,
        temperature=0.5,
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


def _scripted(text: str) -> ScriptedProvider:
    return ScriptedProvider(responses=[make_text_response(text)])


async def _chime(router: LlmMultiVoiceRouter) -> tuple[ChimeIn, ...]:
    return await router.chime(
        question="How is our runway?", answer="About 14 months.", active=_ROSTER
    )


class TestLlmMultiVoiceRouter:
    async def test_confident_specialist_chimes_in(self) -> None:
        provider = _scripted(_voices_json(("CFO", "Watch the Q3 renewal.", 0.9)))
        chimes = await _chime(_router(provider=provider))
        assert len(chimes) == 1
        assert chimes[0].role == "CFO"
        assert chimes[0].name == "Casey"
        assert chimes[0].content == "Watch the Q3 renewal."

    async def test_below_floor_is_filtered(self) -> None:
        provider = _scripted(_voices_json(("CFO", "Minor aside.", 0.5)))
        assert await _chime(_router(provider=provider)) == ()

    async def test_unresolved_role_is_skipped(self) -> None:
        # "Legal" is not on the roster, so it cannot be attributed.
        provider = _scripted(_voices_json(("Legal", "A compliance note.", 0.95)))
        assert await _chime(_router(provider=provider)) == ()

    async def test_capped_at_max_speakers(self) -> None:
        provider = _scripted(
            _voices_json(
                ("CFO", "Finance angle.", 0.95),
                ("CTO", "Tech angle.", 0.9),
            )
        )
        chimes = await _chime(_router(provider=provider, max_speakers=1))
        assert len(chimes) == 1
        # Strongest-first: the CFO (0.95) is kept over the CTO (0.9).
        assert chimes[0].role == "CFO"

    async def test_deduplicated_by_role(self) -> None:
        provider = _scripted(
            _voices_json(
                ("CFO", "First finance take.", 0.9),
                ("CFO", "Second finance take.", 0.85),
            )
        )
        chimes = await _chime(_router(provider=provider))
        assert len(chimes) == 1
        assert chimes[0].content == "First finance take."

    async def test_empty_roster_yields_no_chime(self) -> None:
        provider = _scripted(_voices_json(("CFO", "anything", 0.9)))
        result = await _router(provider=provider).chime(
            question="q", answer="a", active=()
        )
        assert result == ()

    async def test_malformed_json_yields_no_chime(self) -> None:
        assert await _chime(_router(provider=_scripted("not json"))) == ()

    async def test_invalid_schema_yields_no_chime(self) -> None:
        provider = _scripted('{"voices": [{"role": "CFO"}]}')
        assert await _chime(_router(provider=provider)) == ()

    async def test_timeout_yields_no_chime(self) -> None:
        router = _router(provider=_HangingProvider(), timeout_seconds=0.05)
        assert await _chime(router) == ()


class TestSeniorPerRole:
    def test_ties_break_on_name_ascending(self) -> None:
        # Two holders of one role share its authority, so seniority cannot
        # separate them; the collapse must pick the name-alphabetically-first
        # (Alice), matching resolve_agent_for_role's deterministic attribution,
        # NOT the first-encountered (Bob).
        alice = _identity("Alice", "CFO")
        bob = _identity("Bob", "CFO")
        collapsed = _senior_per_role((bob, alice))
        assert len(collapsed) == 1
        assert collapsed[0].name == "Alice"

    def test_one_entry_per_distinct_role(self) -> None:
        collapsed = _senior_per_role((_CFO, _CTO))
        assert {a.role for a in collapsed} == {"CFO", "CTO"}


class TestBuildMultiVoiceRouter:
    def test_unconfigured_model_returns_none(self) -> None:
        config = ChiefOfStaffConfig(multi_voice_model=None)
        assert (
            build_multi_voice_router(
                config=config, provider_registry=ProviderRegistry(drivers={})
            )
            is None
        )

    def test_provider_less_ref_returns_none(self) -> None:
        config = ChiefOfStaffConfig(
            multi_voice_model=NotBlankStr(
                serialize_model_ref(ModelRef(provider="", model_id="example-basic-001"))
            )
        )
        assert (
            build_multi_voice_router(
                config=config, provider_registry=ProviderRegistry(drivers={})
            )
            is None
        )

    def test_unregistered_provider_returns_none(self) -> None:
        config = ChiefOfStaffConfig(
            multi_voice_model=NotBlankStr(
                serialize_model_ref(
                    ModelRef(provider="ghost", model_id="example-basic-001")
                )
            )
        )
        assert (
            build_multi_voice_router(
                config=config, provider_registry=ProviderRegistry(drivers={})
            )
            is None
        )
