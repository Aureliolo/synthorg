"""Unit tests for agent bootstrap from persisted config."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from synthorg.config.schema import AgentConfig
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import FakeClock, make_app_state, mock_of


def _make_agent_config(
    *,
    name: str = "test-agent",
    role: str = "developer",
    department: str = "engineering",
    level: SeniorityLevel = SeniorityLevel.MID,
    model: dict[str, JsonValue] | None = None,
) -> AgentConfig:
    """Build an AgentConfig with sensible defaults."""
    default_model: dict[str, JsonValue] = {
        "provider": "test-provider",
        "model_id": "test-small-001",
    }
    return AgentConfig(
        name=name,
        role=role,
        department=department,
        level=level,
        model=model or default_model,
    )


@pytest.fixture
def registry() -> AgentRegistryService:
    """Create a fresh agent registry."""
    return AgentRegistryService()


@pytest.fixture
def make_config_resolver() -> Callable[..., ConfigResolver]:
    """Build a spec'd ConfigResolver whose ``get_agents`` yields *agents*.

    Autospec via ``mock_of`` makes a method-name typo (``get_agnts``)
    raise loudly instead of silently producing a truthy child mock.

    Each test passes its agent tuple at construction rather than
    mutating ``.return_value`` post hoc, so the injected value stays
    correctly typed as ``ConfigResolver`` (the fixture's contract)
    instead of a bare ``AsyncMock``.
    """

    def _make(agents: tuple[AgentConfig, ...] = ()) -> ConfigResolver:
        resolver: ConfigResolver = mock_of[ConfigResolver](
            get_agents=AsyncMock(return_value=agents),
        )
        return resolver

    return _make


@pytest.mark.unit
class TestBootstrapAgents:
    """Tests for bootstrap_agents()."""

    async def test_registers_agents_from_config(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Happy path: two agent configs produce two registered agents."""
        from synthorg.api.bootstrap import bootstrap_agents

        config_resolver = make_config_resolver(
            (
                _make_agent_config(
                    name="alice", role="developer", department="engineering"
                ),
                _make_agent_config(name="bob", role="designer", department="design"),
            )
        )

        count = await bootstrap_agents(config_resolver, registry)

        assert count == 2
        assert await registry.agent_count() == 2

    async def test_returns_zero_on_empty_config(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Empty agent list produces zero registrations."""
        from synthorg.api.bootstrap import bootstrap_agents

        config_resolver = make_config_resolver()

        count = await bootstrap_agents(config_resolver, registry)

        assert count == 0
        assert await registry.agent_count() == 0

    async def test_re_call_resilience(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Calling bootstrap twice is idempotent.

        Agent ids are derived deterministically from the agent name, so
        the second invocation reconstructs the same ids, collides with
        the already-registered agents, and skips them rather than
        registering duplicates.
        """
        from synthorg.api.bootstrap import bootstrap_agents

        configs = (
            _make_agent_config(name="alice"),
            _make_agent_config(name="bob"),
        )
        config_resolver = make_config_resolver(configs)

        first_count = await bootstrap_agents(config_resolver, registry)
        assert first_count == 2

        second_count = await bootstrap_agents(config_resolver, registry)
        assert second_count == 0
        assert await registry.agent_count() == 2

    async def test_skips_invalid_config_without_aborting(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """One invalid config doesn't prevent valid configs from registering."""
        from synthorg.api.bootstrap import bootstrap_agents

        valid_config = _make_agent_config(name="alice")
        # Model dict missing required 'provider' field -- will fail
        # when constructing ModelConfig inside bootstrap_agents.
        invalid_config = _make_agent_config(
            name="broken",
            model={"model_id": "test-small-001"},
        )

        config_resolver = make_config_resolver((valid_config, invalid_config))

        count = await bootstrap_agents(config_resolver, registry)

        assert count == 1
        assert await registry.agent_count() == 1

    async def test_sets_hiring_date_from_clock(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Hiring date is read from the injected clock, not wall time.

        Pinning the clock removes the midnight-boundary race that a
        direct ``datetime.now()`` read would otherwise carry.
        """
        from synthorg.api.bootstrap import bootstrap_agents

        config_resolver = make_config_resolver((_make_agent_config(name="alice"),))
        clock = FakeClock(start=datetime(2026, 3, 14, 9, 0, tzinfo=UTC))

        await bootstrap_agents(config_resolver, registry, clock=clock)

        agents = await registry.list_active()
        assert len(agents) == 1
        assert agents[0].hiring_date == date(2026, 3, 14)

    async def test_preserves_agent_level(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Agent level from config is preserved in the identity."""
        from synthorg.api.bootstrap import bootstrap_agents

        config_resolver = make_config_resolver(
            (_make_agent_config(name="senior-dev", level=SeniorityLevel.SENIOR),)
        )

        await bootstrap_agents(config_resolver, registry)

        agents = await registry.list_active()
        assert len(agents) == 1
        assert agents[0].level == SeniorityLevel.SENIOR

    async def test_preserves_autonomy_level(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Per-agent autonomy_level is forwarded from config."""
        from synthorg.api.bootstrap import bootstrap_agents
        from synthorg.core.enums import AutonomyLevel

        config = AgentConfig(
            name="autonomous-agent",
            role="developer",
            department="engineering",
            model={"provider": "test-provider", "model_id": "test-small-001"},
            autonomy_level=AutonomyLevel.SEMI,
        )
        config_resolver = make_config_resolver((config,))

        await bootstrap_agents(config_resolver, registry)

        agents = await registry.list_active()
        assert len(agents) == 1
        assert agents[0].autonomy_level == AutonomyLevel.SEMI

    async def test_empty_model_skips_agent(
        self,
        registry: AgentRegistryService,
        make_config_resolver: Callable[..., ConfigResolver],
    ) -> None:
        """Agent with empty model dict is skipped (not registered)."""
        from synthorg.api.bootstrap import bootstrap_agents

        config = AgentConfig(
            name="no-model-agent",
            role="developer",
            department="engineering",
            # model defaults to {} which is falsy
        )
        config_resolver = make_config_resolver((config,))

        count = await bootstrap_agents(config_resolver, registry)

        assert count == 0
        assert await registry.agent_count() == 0


@pytest.mark.unit
class TestMaybeBootstrapAgents:
    """Tests for _maybe_bootstrap_agents()."""

    async def test_returns_early_when_services_missing(self) -> None:
        """Returns immediately when config_resolver is not available."""
        from synthorg.api.lifecycle_helpers.bootstrap import _maybe_bootstrap_agents

        settings_service = mock_of[SettingsService]()
        # config_resolver left unwired -> the presence gate returns early.
        app_state = make_app_state(
            agent_registry=mock_of[AgentRegistryService](),
            settings_service=settings_service,
        )

        await _maybe_bootstrap_agents(app_state)

        # settings_service should not be accessed at all
        settings_service.get_entry.assert_not_called()

    async def test_returns_early_when_setup_not_complete(self) -> None:
        """Returns without bootstrapping when setup_complete != 'true'."""
        from synthorg.api.lifecycle_helpers.bootstrap import _maybe_bootstrap_agents

        entry = AsyncMock()
        entry.value = "false"
        settings_service = mock_of[SettingsService](
            get_entry=AsyncMock(return_value=entry),
        )
        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](),
            agent_registry=mock_of[AgentRegistryService](),
            settings_service=settings_service,
        )

        await _maybe_bootstrap_agents(app_state)

        # get_entry was called but bootstrap_agents should not be invoked
        settings_service.get_entry.assert_called_once_with(
            "api",
            "setup_complete",
        )

    async def test_calls_bootstrap_when_setup_complete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calls bootstrap_agents when setup_complete is 'true'."""
        from synthorg.api.lifecycle_helpers import bootstrap as app_module

        entry = AsyncMock()
        entry.value = "true"
        settings_service = mock_of[SettingsService](
            get_entry=AsyncMock(return_value=entry),
        )
        config_resolver = mock_of[ConfigResolver]()
        agent_registry = mock_of[AgentRegistryService]()
        app_state = make_app_state(
            config_resolver=config_resolver,
            agent_registry=agent_registry,
            settings_service=settings_service,
        )

        mock_bootstrap = AsyncMock(return_value=2)
        monkeypatch.setattr(
            "synthorg.api.bootstrap.bootstrap_agents",
            mock_bootstrap,
        )

        await app_module._maybe_bootstrap_agents(app_state)

        mock_bootstrap.assert_called_once_with(
            config_resolver=config_resolver,
            agent_registry=agent_registry,
        )

    async def test_handles_settings_read_error(self) -> None:
        """Does not crash when settings_service.get_entry raises."""
        from synthorg.api.lifecycle_helpers.bootstrap import _maybe_bootstrap_agents

        settings_service = mock_of[SettingsService](
            get_entry=AsyncMock(side_effect=RuntimeError("db connection lost")),
        )
        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](),
            agent_registry=mock_of[AgentRegistryService](),
            settings_service=settings_service,
        )

        # Should not raise
        await _maybe_bootstrap_agents(app_state)

    async def test_handles_bootstrap_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Does not crash when bootstrap_agents raises."""
        from synthorg.api.lifecycle_helpers import bootstrap as app_module

        entry = AsyncMock()
        entry.value = "true"
        settings_service = mock_of[SettingsService](
            get_entry=AsyncMock(return_value=entry),
        )
        app_state = make_app_state(
            config_resolver=mock_of[ConfigResolver](),
            agent_registry=mock_of[AgentRegistryService](),
            settings_service=settings_service,
        )

        mock_bootstrap = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            "synthorg.api.bootstrap.bootstrap_agents",
            mock_bootstrap,
        )

        # Should not raise
        await app_module._maybe_bootstrap_agents(app_state)
