"""Unit tests for the conflict-resolution boot wiring's judge selection."""

import pytest

from synthorg.api._comms_conflict_wiring import _build_judge_evaluator
from synthorg.communication.conflict_resolution.llm_judge_evaluator import (
    LlmJudgeEvaluator,
)
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.registry import ProviderRegistry

pytestmark = pytest.mark.unit


def test_build_judge_evaluator_none_registry_returns_none() -> None:
    assert _build_judge_evaluator(None, None) is None


def test_build_judge_evaluator_empty_registry_returns_none() -> None:
    registry = ProviderRegistry({})

    assert _build_judge_evaluator(registry, None) is None


def test_build_judge_evaluator_builds_from_first_provider() -> None:
    registry = ProviderRegistry({"test-provider": ScriptedDriver()})

    evaluator = _build_judge_evaluator(registry, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator.metadata.prompt_class_id is PromptPurposeId.CONFLICT_JUDGE
