"""Tests for meeting service auto-wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.api.lifecycle_helpers.meeting_protocol_wiring import (
    build_protocol_registry,
)
from synthorg.communication.meeting._lens_assignment import (
    compute_lens_assignments,
)
from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    MeetingTypeConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.conflict_detection import (
    EmbeddingSimilarityDetector,
)
from synthorg.communication.meeting.enums import (
    ConflictDetectorType,
    MeetingProtocolType,
)
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.structured_phases import (
    StructuredPhasesProtocol,
)
from synthorg.config.schema import RootConfig
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of


def _default_config() -> RootConfig:
    return RootConfig(company_name="test-company")


def _fake_registries() -> tuple[AgentRegistryService, ProviderRegistry]:
    """Return (agent_registry, provider_registry) fakes for wiring tests."""
    return mock_of[AgentRegistryService](), mock_of[ProviderRegistry]()


def _strategy_resolver() -> ConfigResolverProtocol:
    """Return a resolver serving the registered strategy defaults.

    The registry builder reads its policy live, so a test standing in for
    the resolver has to answer the same three keys production reads.
    """

    def _default(key: str) -> str:
        definition = get_registry().get("strategy", key)
        assert definition is not None
        return str(definition.default)

    resolver: ConfigResolverProtocol = mock_of[ConfigResolverProtocol](
        get_enum=AsyncMock(
            spec=ConfigResolverProtocol.get_enum,
            side_effect=lambda _ns, key, enum_cls: enum_cls(_default(key)),
        ),
        get_float=AsyncMock(
            spec=ConfigResolverProtocol.get_float,
            side_effect=lambda _ns, key: float(_default(key)),
        ),
    )
    return resolver


@pytest.mark.unit
class TestBuildProtocolRegistry:
    """Tests for the protocol-registry builder the subsystem activates."""

    async def test_returns_all_three_protocol_types(self) -> None:
        registry = await build_protocol_registry(_strategy_resolver())

        assert set(registry) == set(MeetingProtocolType)

    async def test_protocol_instances_report_correct_type(self) -> None:
        registry = await build_protocol_registry(_strategy_resolver())

        for proto_type, factory in registry.items():
            built = factory(MeetingProtocolConfig(protocol=proto_type))
            assert built.get_protocol_type() == proto_type

    async def test_meeting_type_sub_config_reaches_the_protocol(self) -> None:
        """The wiring path an operator's YAML actually travels.

        Asserting against a registry the test built from the config under
        test proves nothing about production, so this drives the same
        builder the subsystem activates with.
        """
        registry = await build_protocol_registry(_strategy_resolver())
        meeting_type = MeetingTypeConfig(
            name="sprint_planning",
            frequency=MeetingFrequency.WEEKLY,
            protocol_config=MeetingProtocolConfig(
                protocol=MeetingProtocolType.STRUCTURED_PHASES,
                structured_phases=StructuredPhasesConfig(
                    conflict_detector=ConflictDetectorType.EMBEDDING,
                    max_discussion_tokens=4242,
                ),
            ),
        )

        built = registry[MeetingProtocolType.STRUCTURED_PHASES](
            meeting_type.protocol_config,
        )

        assert isinstance(built, StructuredPhasesProtocol)
        assert isinstance(built.conflict_detector, EmbeddingSimilarityDetector)
        assert built.config.max_discussion_tokens == 4242


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

        assignments = compute_lens_assignments(
            ("agent_1", "agent_2", "agent_3"),
            assigner=orchestrator._lens_assigner,
            strategy_config=orchestrator._strategy_config,
        )

        assert assignments is not None
        assert assignments == {
            "agent_1": "contrarian",
            "agent_2": "risk_focused",
            "agent_3": "contrarian",
        }


@pytest.mark.unit
class TestAutoWireMeetings:
    """Tests for auto_wire_meetings main entry point."""

    def test_builds_the_orchestrator_and_no_scheduler(self) -> None:
        """Construction owns the orchestrator alone.

        Every meeting surface binds the orchestrator here, so it is built
        unconditionally. The scheduler is not: it would run ceremonies
        through a caller composed from a provider registry that does not
        exist yet, so its subsystem builds it on the pass where one does.
        """
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
        assert result.meeting_scheduler is None

    async def test_dispatch_is_the_subsystems_even_with_both_registries(
        self,
    ) -> None:
        """One owner for dispatch, whichever wiring path a boot took.

        Composing a real caller here when both registries happen to be
        present would leave two answers to what a meeting turn dispatches
        through, differing by construction order.
        """
        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.communication.meeting.agent_caller import (
            MeetingAgentCallerNotConfiguredError,
        )

        agent_registry, provider_registry = _fake_registries()
        result = auto_wire_meetings(
            effective_config=_default_config(),
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )

        assert result.meeting_orchestrator.has_agent_dispatch is False
        with pytest.raises(MeetingAgentCallerNotConfiguredError) as exc_info:
            await result.meeting_orchestrator._agent_caller(
                "agent-1", "prompt", 100, "meeting-test"
            )
        assert exc_info.value.missing_dependencies == ("meeting_agent_dispatch",)

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
        assert result.meeting_scheduler is None
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
        assert result.meeting_scheduler is None

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
        ``API_MEETINGS_WIRING_DEFERRED`` record.
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

    def test_both_registries_missing_is_warning(self) -> None:
        """Both registries missing is a fault, not the pre-setup case.

        Only a lone missing ``provider_registry`` is the expected
        empty-company / pre-setup state. When ``agent_registry`` is also
        missing, the deferred event must escalate to WARNING so a real
        ``agent_registry`` wiring fault is not suppressed as INFO.
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
                provider_registry=None,
            )

        deferred = [
            e for e in captured if e.get("event") == API_MEETINGS_WIRING_DEFERRED
        ]
        assert len(deferred) == 1
        assert deferred[0]["log_level"] == "warning"
        assert deferred[0]["missing_dependencies"] == (
            "agent_registry",
            "provider_registry",
        )

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


@pytest.mark.unit
class TestOrchestratorStartsWithoutAProtocolRegistry:
    """Construction wires the orchestrator; the subsystem wires its protocols."""

    def test_auto_wired_orchestrator_has_no_registry(self) -> None:
        from synthorg.api.auto_wire_meetings import auto_wire_meetings

        agent_registry, provider_registry = _fake_registries()
        result = auto_wire_meetings(
            effective_config=_default_config(),
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )

        assert result.meeting_orchestrator.has_protocol_registry is False

    async def test_running_a_meeting_names_the_subsystem(self) -> None:
        """The operator is sent to /subsystems, not to a protocol typo."""
        from synthorg.api.auto_wire_meetings import auto_wire_meetings
        from synthorg.communication.meeting.errors import (
            MeetingProtocolNotFoundError,
        )
        from synthorg.communication.meeting.models import MeetingAgenda

        agent_registry, provider_registry = _fake_registries()
        orchestrator = auto_wire_meetings(
            effective_config=_default_config(),
            meeting_orchestrator=None,
            meeting_scheduler=None,
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        ).meeting_orchestrator

        with pytest.raises(
            MeetingProtocolNotFoundError,
            match="meeting_protocol_registry",
        ):
            await orchestrator.run_meeting(
                meeting_type_name="standup",
                protocol_config=MeetingProtocolConfig(),
                agenda=MeetingAgenda(title="Standup"),
                leader_id="leader-id",
                participant_ids=("participant-1",),
                token_budget=1000,
            )


@pytest.mark.unit
class TestWireMeetingOrchestratorError:
    """Tests for error propagation in meeting wiring helpers."""

    def test_orchestrator_creation_failure_propagates(self) -> None:
        from synthorg.api.auto_wire_meetings import _wire_meeting_orchestrator

        agent_registry, provider_registry = _fake_registries()
        with (
            patch(
                "synthorg.api.auto_wire_meetings."
                "build_unconfigured_meeting_agent_caller",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            _wire_meeting_orchestrator(
                agent_registry=agent_registry,
                provider_registry=provider_registry,
                strategy_config=StrategyConfig(),
            )
