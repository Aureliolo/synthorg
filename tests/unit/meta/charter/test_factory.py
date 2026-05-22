"""Unit tests for the charter interview strategy factory."""

import pytest

from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.factory import build_charter_interview_strategy
from synthorg.meta.charter.strategy import LLMCharterInterviewer
from synthorg.meta.errors import UnknownCharterStrategyError
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit


class TestBuildCharterInterviewStrategy:
    def test_llm_discriminator_builds_llm_interviewer(self) -> None:
        strategy = build_charter_interview_strategy(
            CharterConfig(interview_strategy="llm"),
            provider=ScriptedProvider([]),
        )
        assert isinstance(strategy, LLMCharterInterviewer)

    def test_unknown_discriminator_raises(self) -> None:
        # The config field is a Literal, so an unknown value is forced
        # past validation via model_construct to exercise the guard.
        config = CharterConfig.model_construct(interview_strategy="bogus")  # type: ignore[arg-type]
        with pytest.raises(UnknownCharterStrategyError, match="bogus"):
            build_charter_interview_strategy(config, provider=ScriptedProvider([]))
