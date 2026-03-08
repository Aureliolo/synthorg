"""Tests for meeting protocol interface."""

import pytest

from ai_company.communication.meeting.enums import MeetingProtocolType
from ai_company.communication.meeting.models import (  # noqa: TC001
    MeetingAgenda,
    MeetingMinutes,
)
from ai_company.communication.meeting.protocol import (
    AgentCaller,
    ConflictDetector,
    MeetingProtocol,
    TaskCreator,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMeetingProtocolInterface:
    """Tests for MeetingProtocol as a runtime-checkable protocol."""

    def test_is_runtime_checkable(self) -> None:
        """MeetingProtocol must be decorated with @runtime_checkable."""
        assert getattr(MeetingProtocol, "_is_runtime_protocol", False)

    def test_conforming_class_is_instance(self) -> None:
        class _MockProtocol:
            async def run(  # noqa: PLR0913
                self,
                *,
                meeting_id: str,
                agenda: MeetingAgenda,
                leader_id: str,
                participant_ids: tuple[str, ...],
                agent_caller: AgentCaller,
                token_budget: int,
            ) -> MeetingMinutes:
                raise NotImplementedError

            def get_protocol_type(self) -> MeetingProtocolType:
                return MeetingProtocolType.ROUND_ROBIN

        assert isinstance(_MockProtocol(), MeetingProtocol)

    def test_non_conforming_class_is_not_instance(self) -> None:
        class _NotAProtocol:
            pass

        assert not isinstance(_NotAProtocol(), MeetingProtocol)


@pytest.mark.unit
class TestConflictDetectorInterface:
    """Tests for ConflictDetector as a runtime-checkable protocol."""

    def test_is_runtime_checkable(self) -> None:
        assert getattr(ConflictDetector, "_is_runtime_protocol", False)

    def test_conforming_class_is_instance(self) -> None:
        class _MockDetector:
            def detect(self, response_content: str) -> bool:
                return False

        assert isinstance(_MockDetector(), ConflictDetector)

    def test_non_conforming_class_is_not_instance(self) -> None:
        class _NotADetector:
            pass

        assert not isinstance(_NotADetector(), ConflictDetector)


@pytest.mark.unit
class TestTypeAliases:
    """Tests for AgentCaller and TaskCreator type aliases."""

    def test_agent_caller_is_callable(self) -> None:
        # AgentCaller is a type alias — just verify it's importable
        assert AgentCaller is not None

    def test_task_creator_is_callable(self) -> None:
        assert TaskCreator is not None
