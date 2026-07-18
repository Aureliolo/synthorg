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


def test_build_judge_evaluator_builds_on_sole_provider() -> None:
    # A single registered provider is unambiguously the default, so the judge
    # dispatches on it (with the CONFLICT_JUDGE tier archetype as its model).
    registry = ProviderRegistry({"test-provider": ScriptedDriver()})

    evaluator = _build_judge_evaluator(registry, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator.metadata.prompt_class_id is PromptPurposeId.CONFLICT_JUDGE


def test_build_judge_evaluator_uses_explicit_default_provider() -> None:
    # The judge is a system actor: it dispatches on the explicit default
    # provider, not the alphabetically-first registered one.
    default = ScriptedDriver()
    registry = ProviderRegistry(
        {"aaa-provider": ScriptedDriver(), "zzz-provider": default}
    )
    registry.bind_default_provider("zzz-provider")

    evaluator = _build_judge_evaluator(registry, None)

    assert isinstance(evaluator, LlmJudgeEvaluator)
    assert evaluator._provider is default


def test_build_judge_evaluator_none_when_default_ambiguous() -> None:
    # Two providers and no explicit default: the judge stays unwired rather
    # than dispatching on an arbitrary first-registered provider.
    registry = ProviderRegistry(
        {"aaa-provider": ScriptedDriver(), "zzz-provider": ScriptedDriver()}
    )

    assert _build_judge_evaluator(registry, None) is None
