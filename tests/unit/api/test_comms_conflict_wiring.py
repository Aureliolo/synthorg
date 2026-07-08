"""Unit tests for the conflict-resolution boot wiring's judge selection."""

from typing import override

import pytest

from synthorg.api._comms_conflict_wiring import _build_judge_evaluator
from synthorg.communication.conflict_resolution.llm_judge_evaluator import (
    LlmJudgeEvaluator,
)
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.registry import ProviderRegistry

pytestmark = pytest.mark.unit

_JUDGE_MODEL = pin_for(PromptPurposeId.CONFLICT_JUDGE).model


class _PickyDriver(ScriptedDriver):
    """Scripted driver that serves only the single model it is told to."""

    def __init__(self, *, served_model: str) -> None:
        super().__init__()
        self._served_model = served_model

    @override
    def serves_model(self, model: str) -> bool:
        return model == self._served_model


def test_build_judge_evaluator_none_registry_returns_none() -> None:
    assert _build_judge_evaluator(None, None) is None


def test_build_judge_evaluator_empty_registry_returns_none() -> None:
    registry = ProviderRegistry({})

    assert _build_judge_evaluator(registry, None) is None


def test_build_judge_evaluator_builds_when_provider_serves_model() -> None:
    registry = ProviderRegistry({"test-provider": ScriptedDriver()})

    evaluator = _build_judge_evaluator(registry, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator.metadata.prompt_class_id is PromptPurposeId.CONFLICT_JUDGE


def test_build_judge_evaluator_selects_the_serving_provider() -> None:
    # The alphabetically-first provider does NOT serve the pinned model; the
    # judge must resolve by model, not by first-registered.
    serving = _PickyDriver(served_model=_JUDGE_MODEL)
    registry = ProviderRegistry(
        {
            "aaa-provider": _PickyDriver(served_model="some-other-model"),
            "zzz-provider": serving,
        }
    )

    evaluator = _build_judge_evaluator(registry, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator._provider is serving


def test_build_judge_evaluator_none_when_no_provider_serves_model() -> None:
    registry = ProviderRegistry(
        {"only-provider": _PickyDriver(served_model="some-other-model")}
    )

    assert _build_judge_evaluator(registry, None) is None
