"""Tests for meeting protocol configuration models."""

import pytest
import structlog.testing
from pydantic import ValidationError

from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    PositionPapersConfig,
    RoundRobinConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.enums import (
    ConflictDetectorType,
    MeetingProtocolType,
)
from synthorg.observability.events.meeting import (
    MEETING_PROTOCOL_SUB_CONFIG_INACTIVE,
)


@pytest.mark.unit
class TestRoundRobinConfig:
    """Tests for RoundRobinConfig."""

    def test_defaults(self) -> None:
        cfg = RoundRobinConfig()
        assert cfg.max_turns_per_agent == 2
        assert cfg.max_total_turns == 16
        assert cfg.leader_summarizes is True

    def test_custom_values(self) -> None:
        cfg = RoundRobinConfig(
            max_turns_per_agent=3,
            max_total_turns=24,
            leader_summarizes=False,
        )
        assert cfg.max_turns_per_agent == 3
        assert cfg.max_total_turns == 24
        assert cfg.leader_summarizes is False

    def test_frozen(self) -> None:
        cfg = RoundRobinConfig()
        with pytest.raises(ValidationError):
            cfg.max_turns_per_agent = 5  # type: ignore[misc]

    def test_max_turns_per_agent_ge_1(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            RoundRobinConfig(max_turns_per_agent=0)

    def test_max_total_turns_ge_1(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            RoundRobinConfig(max_total_turns=0)


@pytest.mark.unit
class TestPositionPapersConfig:
    """Tests for PositionPapersConfig."""

    def test_defaults(self) -> None:
        cfg = PositionPapersConfig()
        assert cfg.max_tokens_per_position == 300
        assert cfg.synthesizer == "meeting_leader"

    def test_custom_synthesizer(self) -> None:
        cfg = PositionPapersConfig(synthesizer="agent-cto")
        assert cfg.synthesizer == "agent-cto"

    def test_frozen(self) -> None:
        cfg = PositionPapersConfig()
        with pytest.raises(ValidationError):
            cfg.max_tokens_per_position = 500  # type: ignore[misc]

    def test_max_tokens_gt_0(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            PositionPapersConfig(max_tokens_per_position=0)

    def test_synthesizer_not_blank(self) -> None:
        with pytest.raises(ValidationError):
            PositionPapersConfig(synthesizer="  ")


@pytest.mark.unit
class TestStructuredPhasesConfig:
    """Tests for StructuredPhasesConfig."""

    def test_defaults(self) -> None:
        cfg = StructuredPhasesConfig()
        assert cfg.skip_discussion_if_no_conflicts is True
        assert cfg.max_discussion_tokens == 1000

    def test_custom_values(self) -> None:
        cfg = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=False,
            max_discussion_tokens=2000,
        )
        assert cfg.skip_discussion_if_no_conflicts is False
        assert cfg.max_discussion_tokens == 2000

    def test_frozen(self) -> None:
        cfg = StructuredPhasesConfig()
        with pytest.raises(ValidationError):
            cfg.max_discussion_tokens = 500  # type: ignore[misc]

    def test_max_discussion_tokens_gt_0(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            StructuredPhasesConfig(max_discussion_tokens=0)

    def test_conflict_detector_defaults_to_keyword(self) -> None:
        assert StructuredPhasesConfig().conflict_detector is (
            ConflictDetectorType.KEYWORD
        )

    def test_no_embedder_strategy_to_choose(self) -> None:
        """There is one embedding binding, and it is not this one.

        A meeting's conflict detectors score positions with the built-in
        lexical embedder. Offering a local neural alternative here would
        be a second embedder-choice surface next to
        ``memory.embedder_model``, and no shipped image can run one.
        """
        with pytest.raises(ValidationError, match="extra_forbidden"):
            StructuredPhasesConfig.model_validate({"embedder_strategy": "hashing"})


@pytest.mark.unit
class TestMeetingProtocolConfig:
    """Tests for MeetingProtocolConfig."""

    def test_defaults(self) -> None:
        cfg = MeetingProtocolConfig()
        assert cfg.protocol == MeetingProtocolType.ROUND_ROBIN
        assert isinstance(cfg.round_robin, RoundRobinConfig)
        assert isinstance(cfg.position_papers, PositionPapersConfig)
        assert isinstance(cfg.structured_phases, StructuredPhasesConfig)

    def test_custom_protocol(self) -> None:
        cfg = MeetingProtocolConfig(
            protocol=MeetingProtocolType.POSITION_PAPERS,
        )
        assert cfg.protocol == MeetingProtocolType.POSITION_PAPERS

    def test_frozen(self) -> None:
        cfg = MeetingProtocolConfig()
        with pytest.raises(ValidationError):
            cfg.protocol = MeetingProtocolType.STRUCTURED_PHASES  # type: ignore[misc]

    def test_nested_configs_accessible(self) -> None:
        cfg = MeetingProtocolConfig(
            round_robin=RoundRobinConfig(max_turns_per_agent=5),
            position_papers=PositionPapersConfig(max_tokens_per_position=500),
            structured_phases=StructuredPhasesConfig(
                skip_discussion_if_no_conflicts=False,
            ),
        )
        assert cfg.round_robin.max_turns_per_agent == 5
        assert cfg.position_papers.max_tokens_per_position == 500
        assert cfg.structured_phases.skip_discussion_if_no_conflicts is False

    def test_auto_create_tasks_default(self) -> None:
        cfg = MeetingProtocolConfig()
        assert cfg.auto_create_tasks is True

    def test_auto_create_tasks_disabled(self) -> None:
        cfg = MeetingProtocolConfig(auto_create_tasks=False)
        assert cfg.auto_create_tasks is False

    def test_customised_inactive_sub_config_warns(self) -> None:
        with structlog.testing.capture_logs() as logs:
            MeetingProtocolConfig(
                protocol=MeetingProtocolType.ROUND_ROBIN,
                position_papers=PositionPapersConfig(max_tokens_per_position=500),
            )

        warned = [
            log
            for log in logs
            if log.get("event") == MEETING_PROTOCOL_SUB_CONFIG_INACTIVE
        ]
        assert len(warned) == 1
        assert warned[0]["inactive_sub_configs"] == ["position_papers"]

    def test_default_round_trip_does_not_warn(self) -> None:
        """A persisted-and-reloaded default config is not a customisation.

        ``model_dump`` writes every nested default out explicitly and
        ``model_validate`` marks each one as set, so the reloaded config
        reports every sub-config field as set while holding the values it
        shipped with. Nothing was tuned, so nothing may be reported.
        """
        dumped = MeetingProtocolConfig().model_dump()

        with structlog.testing.capture_logs() as logs:
            reloaded = MeetingProtocolConfig.model_validate(dumped)

        assert reloaded == MeetingProtocolConfig()
        assert [
            log
            for log in logs
            if log.get("event") == MEETING_PROTOCOL_SUB_CONFIG_INACTIVE
        ] == []

    def test_round_trip_still_warns_on_a_real_customisation(self) -> None:
        """The round trip must not mask a customisation either."""
        dumped = MeetingProtocolConfig(
            protocol=MeetingProtocolType.ROUND_ROBIN,
            structured_phases=StructuredPhasesConfig(max_discussion_tokens=4096),
        ).model_dump()

        with structlog.testing.capture_logs() as logs:
            MeetingProtocolConfig.model_validate(dumped)

        warned = [
            log
            for log in logs
            if log.get("event") == MEETING_PROTOCOL_SUB_CONFIG_INACTIVE
        ]
        assert len(warned) == 1
        assert warned[0]["inactive_sub_configs"] == ["structured_phases"]
