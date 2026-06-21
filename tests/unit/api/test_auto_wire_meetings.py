"""Tests for meeting service auto-wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import (
    PassthroughParticipantResolver,
    RegistryParticipantResolver,
)
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.config.schema import RootConfig
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.registry import ProviderRegistry
from tests._shared import mock_of


def _default_config() -> RootConfig:
    return RootConfig(company_name="test-company")


def _fake_registries() -> tuple[AgentRegistryService, ProviderRegistry]:
    """Return (agent_registry, provider_registry) fakes for wiring tests."""
    return mock_of[AgentRegistryService](), mock_of[ProviderRegistry]()


@pytest.mark.unit
class TestBuildProtocolRegistry:
    """Tests for _build_protocol_registry helper."""

    def test_returns_all_three_protocol_types(self) -> None:
        from synthorg.api.auto_wire_meetings import _build_protocol_registry

        registry = _build_protocol_registry()

        assert MeetingProtocolType.ROUND_ROBIN in registry
        assert MeetingProtocolType.POSITION_PAPERS in registry
        assert MeetingProtocolType.STRUCTURED_PHASES in registry
        assert len(registry) == 3

    def test_protocol_instances_report_correct_type(self) -> None:
        from synthorg.api.auto_wire_meetings import _build_protocol_registry

        registry = _build_protocol_registry()

        for proto_type, proto_impl in registry.items():
            assert proto_impl.get_protocol_type() == proto_type


@pytest.mark.unit
class TestWireMeetingOrchestrator:
    """Tests for _wire_meeting_orchestrator helper."""

    def test_creates_valid_orchestrator(self) -> None:
        from synthorg.api.auto_wire_meetings import _wire_meeting_orchestrator

        agent_registry, provider_registry = _fake_registries()
        orchestrator = _wire_meeting_orchestrator(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
            strategy_config=StrategyConfig(),
        )

        assert isinstance(orchestrator, MeetingOrchestrator)
        assert orchestrator.get_records() == ()

    def test_wires_lens_assigner_for_diverse_lenses(self) -> None:
        from synthorg.api.auto_wire_meetings import _wire_meeting_orchestrator

        agent_registry, provider_registry = _fake_registries()
        orchestrator = _wire_meeting_orchestrator(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
            strategy_config=StrategyConfig(
                default_lenses=("contrarian", "risk_focused"),
            ),
        )

        assignments = orchestrator._compute_lens_assignments(
            ("agent_1", "agent_2", "agent_3"),
        )

        assert assignments is not None
        assert assignments == {
            "agent_1": "contrarian",
            "agent_2": "risk_focused",
            "agent_3": "contrarian",
        }


@pytest.mark.unit
class TestWireMeetingScheduler:
    """Tests for _wire_meeting_scheduler helper."""

    def test_uses_registry_resolver_when_available(self) -> None:
        from synthorg.api.auto_wire_meetings import (
            _wire_meeting_orchestrator,
            _wire_meeting_scheduler,
        )

        config = _default_config()
        agent_registry, provider_registry = _fake_registries()
        orchestrator = _wire_meeting_orchestrator(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
            strategy_config=StrategyConfig(),
        )
        registry = MagicMock()

        scheduler = _wire_meeting_scheduler(config, orchestrator, registry)

        assert isinstance(scheduler, MeetingScheduler)
        assert isinstance(scheduler._resolver, RegistryParticipantResolver)

    def test_uses_passthrough_resolver_when_no_registry(self) -> None:
        from synthorg.api.auto_wire_meetings import (
            _wire_meeting_orchestrator,
            _wire_meeting_scheduler,
        )

        config = _default_config()
        agent_registry, provider_registry = _fake_registries()
        orchestrator = _wire_meeting_orchestrator(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
            strategy_config=StrategyConfig(),
        )

        scheduler = _wire_meeting_scheduler(config, orchestrator, None)

        assert isinstance(scheduler, MeetingScheduler)
        assert isinstance(scheduler._resolver, PassthroughParticipantResolver)


@pytest.mark.unit
class TestAutoWireMeetings:
    """Tests for auto_wire_meetings main entry point."""

    def test_creates_both_services(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        config = _default_config()
        agent_registry, provider_registry = _fake_registries()
        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )

        assert isinstance(result.meeting_orchestrator, MeetingOrchestrator)
        assert isinstance(result.meeting_scheduler, MeetingScheduler)

    async def test_wires_unconfigured_caller_when_registries_missing(
        self,
    ) -> None:
        """Orchestrator still wires without registries; call raises loudly."""
        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.communication.meeting.agent_caller import (
            MeetingAgentCallerNotConfiguredError,
        )

        config = _default_config()
        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=None,
            provider_registry=None,
        )

        assert isinstance(result.meeting_orchestrator, MeetingOrchestrator)
        # Schedulers must stay ``None`` when the caller is guaranteed to
        # raise -- running scheduled meetings against an unconfigured
        # caller would only produce background noise.
        assert result.meeting_scheduler is None
        assert result.ceremony_scheduler is None
        caller = result.meeting_orchestrator._agent_caller
        with pytest.raises(MeetingAgentCallerNotConfiguredError) as exc_info:
            await caller("agent-1", "prompt", 100, "meeting-test")
        # Error carries agent_id and names both missing dependencies so
        # operators can act without parsing the message string.
        assert exc_info.value.agent_id == "agent-1"
        assert set(exc_info.value.missing_dependencies) == {
            "agent_registry",
            "provider_registry",
        }
        assert "agent_registry" in str(exc_info.value)
        assert "provider_registry" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("agent_registry_value", "provider_registry_value", "expected_missing"),
        [
            pytest.param(
                None,
                MagicMock(spec=ProviderRegistry),
                ("agent_registry",),
                id="only-agent-missing",
            ),
            pytest.param(
                MagicMock(spec=AgentRegistryService),
                None,
                ("provider_registry",),
                id="only-provider-missing",
            ),
        ],
    )
    async def test_partial_missing_registries_names_exact_gap(
        self,
        agent_registry_value: MagicMock | None,
        provider_registry_value: MagicMock | None,
        expected_missing: tuple[str, ...],
    ) -> None:
        """Only the actually-missing dependency appears in the error."""
        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.communication.meeting.agent_caller import (
            MeetingAgentCallerNotConfiguredError,
        )

        config = _default_config()
        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=agent_registry_value,
            provider_registry=provider_registry_value,
        )

        assert isinstance(result.meeting_orchestrator, MeetingOrchestrator)
        assert result.meeting_scheduler is None
        assert result.ceremony_scheduler is None
        caller = result.meeting_orchestrator._agent_caller
        with pytest.raises(MeetingAgentCallerNotConfiguredError) as exc_info:
            await caller("agent-1", "prompt", 100, "meeting-test")
        assert exc_info.value.missing_dependencies == expected_missing

    def test_preserves_explicit_orchestrator(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        config = _default_config()
        explicit_orch = MagicMock(spec=MeetingOrchestrator)

        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=explicit_orch,
            meeting_scheduler=None,
            agent_registry=None,
            provider_registry=None,
        )

        assert result.meeting_orchestrator is explicit_orch
        assert isinstance(result.meeting_scheduler, MeetingScheduler)

    def test_preserves_explicit_scheduler(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        config = _default_config()
        # Cannot use spec=MeetingScheduler: PEP 649 deferred
        # annotation for MeetingsConfig causes NameError in inspect.
        explicit_sched = MagicMock()
        agent_registry, provider_registry = _fake_registries()

        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=None,
            meeting_scheduler=explicit_sched,
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )

        assert isinstance(result.meeting_orchestrator, MeetingOrchestrator)
        assert result.meeting_scheduler is explicit_sched

    def test_preserves_both_explicit(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        config = _default_config()
        explicit_orch = MagicMock(spec=MeetingOrchestrator)
        # Cannot use spec=MeetingScheduler: PEP 649 deferred
        # annotation for MeetingsConfig causes NameError in inspect.
        explicit_sched = MagicMock()

        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=explicit_orch,
            meeting_scheduler=explicit_sched,
            agent_registry=None,
            provider_registry=None,
        )

        assert result.meeting_orchestrator is explicit_orch
        assert result.meeting_scheduler is explicit_sched

    def test_pre_setup_emits_single_info_deferred_event(self) -> None:
        """Pre-setup deferral (provider_registry None) is one INFO, no WARNING.

        On a clean pre-setup boot the provider registry is not yet wired;
        the deferred meeting stack must produce exactly one INFO
        ``API_MEETINGS_WIRING_DEFERRED`` record rather than the former
        two WARNINGs.
        """
        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.observability.events.api import API_MEETINGS_WIRING_DEFERRED

        config = _default_config()
        with structlog.testing.capture_logs() as captured:
            auto_wire_meetings(
                effective_config=config,
                meeting_orchestrator=None,
                meeting_scheduler=None,
                agent_registry=MagicMock(spec=AgentRegistryService),
                provider_registry=None,
            )

        deferred = [
            e for e in captured if e.get("event") == API_MEETINGS_WIRING_DEFERRED
        ]
        assert len(deferred) == 1
        assert deferred[0]["log_level"] == "info"
        assert deferred[0]["missing_dependencies"] == ("provider_registry",)
        assert deferred[0]["schedulers_deferred"] is True
        # No WARNING carrying missing_dependencies survives the consolidation.
        warns = [
            e
            for e in captured
            if e.get("log_level") == "warning" and "missing_dependencies" in e
        ]
        assert warns == []

    def test_post_setup_missing_dependency_is_warning(self) -> None:
        """A missing dep while provider_registry is present is a WARNING.

        Post-setup the provider registry is wired; a still-missing
        dependency is unexpected and surfaces at WARNING, not INFO.
        """
        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.observability.events.api import API_MEETINGS_WIRING_DEFERRED

        config = _default_config()
        with structlog.testing.capture_logs() as captured:
            auto_wire_meetings(
                effective_config=config,
                meeting_orchestrator=None,
                meeting_scheduler=None,
                agent_registry=None,
                provider_registry=MagicMock(spec=ProviderRegistry),
            )

        deferred = [
            e for e in captured if e.get("event") == API_MEETINGS_WIRING_DEFERRED
        ]
        assert len(deferred) == 1
        assert deferred[0]["log_level"] == "warning"
        assert deferred[0]["missing_dependencies"] == ("agent_registry",)

    def test_logs_auto_wire_events(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        config = _default_config()
        agent_registry, provider_registry = _fake_registries()

        with structlog.testing.capture_logs() as captured:
            auto_wire_meetings(
                effective_config=config,
                meeting_orchestrator=None,
                meeting_scheduler=None,
                agent_registry=agent_registry,
                provider_registry=provider_registry,
            )

        services = [e.get("service") for e in captured]
        assert "meeting_orchestrator" in services
        assert "meeting_scheduler" in services

    async def test_real_caller_end_to_end_with_both_registries(self) -> None:
        """Wiring both registries produces a caller that dispatches real LLM calls.

        Integration test: construct auto_wire_meetings with fake (but
        shape-correct) agent registry + provider registry, invoke the
        wired ``agent_caller`` directly, and assert it reaches the
        provider and returns an ``AgentResponse`` with provider-sourced
        tokens/cost.  Catches wiring regressions that pure unit tests
        of each layer miss.
        """
        from datetime import date
        from uuid import uuid4

        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.communication.meeting.models import AgentResponse
        from synthorg.core.agent import (
            AgentIdentity,
            ModelConfig,
            PersonalityConfig,
        )
        from synthorg.core.completion_enums import FinishReason
        from synthorg.core.types import NotBlankStr
        from synthorg.hr.enums import AgentStatus
        from synthorg.hr.seniority import SeniorityLevel
        from synthorg.providers.models import CompletionResponse, TokenUsage

        identity = AgentIdentity(
            id=uuid4(),
            name=NotBlankStr("Sarah Chen"),
            role=NotBlankStr("engineer"),
            department=NotBlankStr("engineering"),
            level=SeniorityLevel.MID,
            personality=PersonalityConfig(
                communication_style=NotBlankStr("concise"),
            ),
            model=ModelConfig(
                provider=NotBlankStr("test-provider"),
                model_id=NotBlankStr("test-medium-001"),
            ),
            hiring_date=date(2026, 1, 1),
            status=AgentStatus.ACTIVE,
        )
        agent_registry = MagicMock()
        agent_registry.get = AsyncMock(return_value=identity)

        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=CompletionResponse(
                content="I recommend a task queue.",
                finish_reason=FinishReason.STOP,
                usage=TokenUsage(
                    input_tokens=12,
                    output_tokens=7,
                    cost=0.0005,
                ),
                model=NotBlankStr("test-medium-001"),
            )
        )
        provider_registry = MagicMock()
        provider_registry.get = MagicMock(return_value=provider)

        result = auto_wire_meetings(
            effective_config=_default_config(),
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )

        caller = result.meeting_orchestrator._agent_caller
        response = await caller(str(identity.id), "What is next?", 256, "meeting-test")

        assert isinstance(response, AgentResponse)
        assert response.content == "I recommend a task queue."
        assert response.input_tokens == 12
        assert response.output_tokens == 7
        provider_registry.get.assert_called_once_with("test-provider")
        provider.complete.assert_awaited_once()

    def test_with_agent_registry(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        config = _default_config()
        registry = MagicMock()
        provider_registry = MagicMock()

        result = auto_wire_meetings(
            effective_config=config,
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=registry,
            provider_registry=provider_registry,
        )

        assert isinstance(result.meeting_orchestrator, MeetingOrchestrator)
        assert isinstance(result.meeting_scheduler, MeetingScheduler)
        assert isinstance(
            result.meeting_scheduler._resolver,
            RegistryParticipantResolver,
        )


@pytest.mark.unit
class TestWireMeetingOrchestratorError:
    """Tests for error propagation in meeting wiring helpers."""

    def test_orchestrator_creation_failure_propagates(self) -> None:
        from synthorg.api.auto_wire_meetings import _wire_meeting_orchestrator

        agent_registry, provider_registry = _fake_registries()
        with (
            patch(
                "synthorg.api.auto_wire_meetings._build_protocol_registry",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            _wire_meeting_orchestrator(
                agent_registry=agent_registry,
                provider_registry=provider_registry,
                strategy_config=StrategyConfig(),
            )

    def test_scheduler_creation_failure_propagates(self) -> None:
        from synthorg.api.auto_wire_meetings import (
            _wire_meeting_orchestrator,
            _wire_meeting_scheduler,
        )

        config = _default_config()
        agent_registry, provider_registry = _fake_registries()
        orchestrator = _wire_meeting_orchestrator(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
            strategy_config=StrategyConfig(),
        )

        with (
            patch(
                "synthorg.api.auto_wire_meetings._select_participant_resolver",
                side_effect=RuntimeError("resolver-error"),
            ),
            pytest.raises(RuntimeError, match="resolver-error"),
        ):
            _wire_meeting_scheduler(config, orchestrator, None)
