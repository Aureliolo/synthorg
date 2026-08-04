"""Unit tests for the conflict-resolution boot wiring's judge selection."""

import pytest

from synthorg.api._comms_conflict_wiring import _build_judge_evaluator
from synthorg.communication.conflict_resolution.llm_judge_evaluator import (
    LlmJudgeEvaluator,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.registry import ProviderRegistry
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def test_judge_is_built_with_its_prompt_purpose() -> None:
    app_state = make_app_state(
        provider_registry=ProviderRegistry({"test-provider": ScriptedDriver()}),
    )

    evaluator = _build_judge_evaluator(app_state, None, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator.metadata.prompt_class_id is PromptPurposeId.CONFLICT_JUDGE


def test_judge_resolves_the_named_connection_not_the_first() -> None:
    """The selector answers by name, so no connection is picked for it."""
    chosen = ScriptedDriver()
    app_state = make_app_state(
        provider_registry=ProviderRegistry(
            {"aaa-provider": ScriptedDriver(), "zzz-provider": chosen}
        ),
    )

    evaluator = _build_judge_evaluator(app_state, None, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator._connections("zzz-provider") is chosen


def test_judge_follows_a_swapped_registry() -> None:
    """A provider reload replaces the registry; the judge must not strand.

    The selector reads the wired registry per call rather than capturing one
    at boot, so a connection registered after the judge was built resolves,
    and a judge built before any registry existed still comes online.
    """
    app_state = make_app_state(provider_registry=ProviderRegistry({}))
    evaluator = _build_judge_evaluator(app_state, None, None)
    assert isinstance(evaluator, LlmJudgeEvaluator)

    with pytest.raises(DriverNotRegisteredError):
        evaluator._connections("late-provider")

    late = ScriptedDriver()
    app_state.swap_provider_registry(ProviderRegistry({"late-provider": late}))

    assert evaluator._connections("late-provider") is late


def test_judge_raises_when_no_registry_is_wired() -> None:
    """No registry at all is unavailability, surfaced rather than swallowed."""
    app_state = make_app_state()
    evaluator = _build_judge_evaluator(app_state, None, None)
    assert isinstance(evaluator, LlmJudgeEvaluator)

    with pytest.raises(ServiceUnavailableError, match="Provider Registry"):
        evaluator._connections("test-provider")
