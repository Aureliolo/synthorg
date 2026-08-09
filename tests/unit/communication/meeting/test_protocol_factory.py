"""Tests for per-meeting protocol construction.

The subject here is reachability: a meeting type's ``protocol_config``
sub-config must reach the protocol instance that acts on it. Every
assertion below is driven through ``build_protocol_factories`` rather
than a hand-built registry, because a hand-built registry derived from
the config under test is exactly what hid this defect.
"""

import pytest

from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    PositionPapersConfig,
    RoundRobinConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.conflict_detection import (
    EmbeddingSimilarityDetector,
    KeywordConflictDetector,
    StructuredComparisonDetector,
)
from synthorg.communication.meeting.enums import (
    ConflictDetectorType,
    MeetingProtocolType,
)
from synthorg.communication.meeting.hooks import (
    ConsensusVelocityHook,
    PremortemHook,
)
from synthorg.communication.meeting.position_papers import (
    PositionPapersProtocol,
)
from synthorg.communication.meeting.protocol_factory import (
    build_protocol_factories,
)
from synthorg.communication.meeting.round_robin import RoundRobinProtocol
from synthorg.communication.meeting.structured_phases import (
    StructuredPhasesProtocol,
)
from tests._shared import mock_of


@pytest.mark.unit
class TestBuildProtocolFactories:
    """Every declared protocol type has a factory."""

    def test_covers_every_protocol_type(self) -> None:
        factories = build_protocol_factories()

        assert set(factories) == set(MeetingProtocolType)

    def test_each_factory_builds_its_own_protocol_type(self) -> None:
        factories = build_protocol_factories()

        for protocol_type, factory in factories.items():
            built = factory(MeetingProtocolConfig(protocol=protocol_type))
            assert built.get_protocol_type() == protocol_type


@pytest.mark.unit
class TestSubConfigReachesTheProtocol:
    """The per-meeting sub-config reaches the instance that acts on it."""

    def test_round_robin_sub_config_is_applied(self) -> None:
        factories = build_protocol_factories()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.ROUND_ROBIN,
            round_robin=RoundRobinConfig(
                max_turns_per_agent=7,
                max_total_turns=3,
                leader_summarizes=False,
            ),
        )

        built = factories[MeetingProtocolType.ROUND_ROBIN](config)

        assert isinstance(built, RoundRobinProtocol)
        assert built._config == config.round_robin

    def test_position_papers_sub_config_is_applied(self) -> None:
        factories = build_protocol_factories()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.POSITION_PAPERS,
            position_papers=PositionPapersConfig(
                max_tokens_per_position=999,
                synthesizer="agent-a",
            ),
        )

        built = factories[MeetingProtocolType.POSITION_PAPERS](config)

        assert isinstance(built, PositionPapersProtocol)
        assert built._config == config.position_papers

    def test_structured_phases_sub_config_is_applied(self) -> None:
        factories = build_protocol_factories()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            structured_phases=StructuredPhasesConfig(max_discussion_tokens=4242),
        )

        built = factories[MeetingProtocolType.STRUCTURED_PHASES](config)

        assert isinstance(built, StructuredPhasesProtocol)
        assert built._config == config.structured_phases

    @pytest.mark.parametrize(
        ("detector_type", "expected"),
        [
            (ConflictDetectorType.KEYWORD, KeywordConflictDetector),
            (ConflictDetectorType.STRUCTURED, StructuredComparisonDetector),
            (ConflictDetectorType.EMBEDDING, EmbeddingSimilarityDetector),
        ],
    )
    def test_configured_conflict_detector_is_the_one_built(
        self,
        detector_type: ConflictDetectorType,
        expected: type[object],
    ) -> None:
        factories = build_protocol_factories()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            structured_phases=StructuredPhasesConfig(
                conflict_detector=detector_type,
            ),
        )

        built = factories[MeetingProtocolType.STRUCTURED_PHASES](config)

        assert isinstance(built, StructuredPhasesProtocol)
        assert isinstance(built._conflict_detector, expected)

    def test_each_call_builds_an_independently_configured_instance(self) -> None:
        factories = build_protocol_factories()
        factory = factories[MeetingProtocolType.ROUND_ROBIN]

        first = factory(
            MeetingProtocolConfig(round_robin=RoundRobinConfig(max_total_turns=2)),
        )
        second = factory(
            MeetingProtocolConfig(round_robin=RoundRobinConfig(max_total_turns=9)),
        )

        assert first is not second
        assert isinstance(first, RoundRobinProtocol)
        assert isinstance(second, RoundRobinProtocol)
        assert first._config.max_total_turns == 2
        assert second._config.max_total_turns == 9


@pytest.mark.unit
class TestStrategyHooksAreBuiltOnce:
    """Hooks are wiring-time singletons the factories close over."""

    def test_hooks_reach_every_structured_phases_instance(self) -> None:
        consensus = mock_of[ConsensusVelocityHook]()
        premortem = mock_of[PremortemHook]()
        factories = build_protocol_factories(
            consensus_hook=consensus,
            premortem_hook=premortem,
        )
        factory = factories[MeetingProtocolType.STRUCTURED_PHASES]

        first = factory(MeetingProtocolConfig())
        second = factory(MeetingProtocolConfig())

        assert isinstance(first, StructuredPhasesProtocol)
        assert isinstance(second, StructuredPhasesProtocol)
        assert first._consensus_hook is consensus
        assert second._consensus_hook is consensus
        assert first._premortem_hook is premortem
        assert second._premortem_hook is premortem
