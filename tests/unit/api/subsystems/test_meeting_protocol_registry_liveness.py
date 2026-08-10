"""A strategy settings write reaches the next meeting without a restart.

The consensus-velocity and premortem hooks are baked into the protocol
factories when they are built, which used to happen once at wiring time: an
operator could change the organisation's policy and every meeting for the life
of the process kept the boot values. These drive the SHIPPED declarations
through a real reconcile pass, because the defect was a registry built by the
wrong owner rather than a builder that read the wrong thing.
"""

from collections.abc import Sequence

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.api.subsystems.runtime import reconcile_subsystems
from synthorg.api.subsystems.spec import CapabilityId, SubsystemSpec
from synthorg.communication.meeting.agent_caller import (
    build_unconfigured_meeting_agent_caller,
)
from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.structured_phases import StructuredPhasesProtocol
from synthorg.communication.state import CommunicationStateSlice
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.models import ConsensusAction, PremortemParticipation
from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingEntry, SettingValue
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_SPEC_NAME = "meeting_protocol_registry"


class _Values:
    """The settings a pass reads, rewritable between passes.

    Moving a value here is what an operator's write looks like from the
    reconciler's side; unset keys fall back to their registered default so
    the rest of the pass behaves as it would on a fresh install.
    """

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def get(self, namespace: str, key: str) -> SettingValue:
        """Resolve one setting from the mutable map.

        Returns:
            The current value, or the registered default for an unset key.
        """
        definition = get_registry().get(namespace, key)
        default = "" if definition is None else str(definition.default or "")
        return SettingValue(
            namespace=SettingNamespace(namespace),
            key=NotBlankStr(key),
            value=self.values.get(f"{namespace}.{key}", default),
            source=SettingSource.DATABASE,
        )

    async def get_namespace(self, namespace: str) -> tuple[SettingEntry, ...]:
        """Resolve a whole namespace, as the config overlay reads it.

        Returns:
            Every registered setting in *namespace* at its current value.
        """
        entries: list[SettingEntry] = []
        for definition in get_registry().list_namespace(namespace):
            resolved = await self.get(namespace, definition.key)
            entries.append(
                SettingEntry(
                    definition=definition,
                    value=resolved.value,
                    source=SettingSource.DATABASE,
                )
            )
        return tuple(entries)


def _spec() -> SubsystemSpec:
    """Return the shipped declaration.

    Returns:
        The spec, so a rename fails here rather than silently passing.
    """
    for spec in SUBSYSTEMS:
        if spec.name == _SPEC_NAME:
            return spec
    msg = f"no subsystem declared as {_SPEC_NAME!r}"
    raise AssertionError(msg)


def _app_state(
    values: dict[str, str] | None = None,
) -> tuple[AppState, _Values, MeetingOrchestrator]:
    """Build state with an orchestrator and a rewritable settings source.

    The orchestrator is constructed the way the construction phase builds
    it: no protocol registry, because the subsystem owns installing one.

    Returns:
        The state, the settings source, and the orchestrator.
    """
    source = _Values(values or {})
    config = RootConfig(company_name="test")
    settings_service = mock_of[SettingsServiceProtocol](
        get=source.get, get_namespace=source.get_namespace
    )
    resolver = ConfigResolver(settings_service=settings_service, config=config)
    orchestrator = MeetingOrchestrator(
        agent_caller=build_unconfigured_meeting_agent_caller(
            missing_dependencies=("provider_registry",),
        ),
    )
    app_state = make_app_state(config=config)
    app_state.wire(
        SettingsStateSlice,
        config_resolver=resolver,
        settings_service=settings_service,
    )
    app_state.wire(
        CommunicationStateSlice,
        meeting_orchestrator=orchestrator,
    )
    return app_state, source, orchestrator


async def _pass(app_state: AppState) -> None:
    """Run one reconcile pass, the way a settings write drives it."""
    await reconcile_subsystems(app_state, trigger="test")


def _consensus_hook_verdict(
    orchestrator: MeetingOrchestrator,
    positions: Sequence[str],
) -> bool:
    """Ask the installed structured-phases protocol about *positions*.

    Builds the protocol the way a meeting would, then runs the consensus
    hook it closed over, so the assertion is about behaviour rather than
    about which object was stored.

    Returns:
        Whether the hook reports premature convergence.
    """
    factory = orchestrator._protocol_registry[MeetingProtocolType.STRUCTURED_PHASES]
    protocol = factory(
        MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            structured_phases=StructuredPhasesConfig(),
        )
    )
    assert isinstance(protocol, StructuredPhasesProtocol)
    hook = protocol._consensus_hook
    assert hook is not None
    return hook(tuple(positions))


class TestDeclaration:
    """The declaration has to buy a rebuild, not only a first activation."""

    def test_declares_the_strategy_settings(self) -> None:
        assert set(_spec().settings) == {
            "strategy.consensus_velocity_action",
            "strategy.consensus_velocity_threshold",
            "strategy.premortem_participants",
        }

    def test_rebuild_ships_with_a_teardown(self) -> None:
        spec = _spec()
        assert spec.rebuild_on_change is True
        assert spec.deactivate is not None

    def test_waits_for_the_resolver_it_reads_through(self) -> None:
        """A pass with no resolver would snapshot every key as unreadable."""
        assert CapabilityId.SETTINGS_RESOLVER in _spec().requires

    def test_nothing_consumes_the_capability(self) -> None:
        """No cascade: a consumer would need its own teardown and rebuild."""
        consumers = [
            spec.name
            for spec in SUBSYSTEMS
            if CapabilityId.MEETING_PROTOCOL_REGISTRY in spec.requires
        ]
        assert consumers == []


class TestActivation:
    """Boot installs the registry; without it a meeting cannot run."""

    async def test_orchestrator_starts_with_no_registry(self) -> None:
        orchestrator = _app_state()[2]
        assert orchestrator.has_protocol_registry is False

    async def test_first_pass_installs_it(self) -> None:
        app_state, _source, orchestrator = _app_state()

        await _pass(app_state)

        assert orchestrator.has_protocol_registry is True

    async def test_a_pass_with_no_resolver_leaves_it_waiting(self) -> None:
        """Waiting is the honest answer, not a registry built from defaults."""
        app_state, _source, orchestrator = _app_state()
        app_state.wire(SettingsStateSlice, config_resolver=None)

        await _pass(app_state)

        assert orchestrator.has_protocol_registry is False


class TestStrategyWriteReachesTheNextMeeting:
    """The #2750 acceptance case, driven through the reconciler."""

    #: Four near-identical positions: converged under a lenient threshold,
    #: and still converged under a strict one, so only the threshold moves
    #: the verdict.
    _CONVERGED = ("We should ship it now.",) * 4

    async def test_threshold_write_changes_the_hook_verdict(self) -> None:
        app_state, source, orchestrator = _app_state(
            {"strategy.consensus_velocity_threshold": "0.85"},
        )
        await _pass(app_state)
        assert _consensus_hook_verdict(orchestrator, self._CONVERGED) is True

        # A threshold nothing can exceed leaves every meeting reading as
        # diverse, which is the observable the operator changed.
        source.values["strategy.consensus_velocity_threshold"] = "1.0"
        await _pass(app_state)

        assert _consensus_hook_verdict(orchestrator, self._CONVERGED) is False

    async def test_the_registry_instance_is_replaced(self) -> None:
        """A rebuild, not a nudge: the factories are new objects."""
        app_state, source, orchestrator = _app_state(
            {"strategy.premortem_participants": PremortemParticipation.ALL.value},
        )
        await _pass(app_state)
        first = orchestrator._protocol_registry[MeetingProtocolType.STRUCTURED_PHASES]

        source.values["strategy.premortem_participants"] = (
            PremortemParticipation.NONE.value
        )
        await _pass(app_state)

        second = orchestrator._protocol_registry[MeetingProtocolType.STRUCTURED_PHASES]
        assert second is not first

    async def test_an_unchanged_pass_leaves_the_registry_alone(self) -> None:
        """Level-triggered: converged state costs a probe, not a rebuild."""
        app_state, _source, orchestrator = _app_state(
            {
                "strategy.consensus_velocity_action": (
                    ConsensusAction.DEVIL_ADVOCATE.value
                ),
            },
        )
        await _pass(app_state)
        first = orchestrator._protocol_registry[MeetingProtocolType.STRUCTURED_PHASES]

        await _pass(app_state)

        assert (
            orchestrator._protocol_registry[MeetingProtocolType.STRUCTURED_PHASES]
            is first
        )
