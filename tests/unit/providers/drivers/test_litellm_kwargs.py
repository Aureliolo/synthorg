"""Tests for the typed LiteLLM ``acompletion`` kwargs assembly."""

import pytest

from synthorg.core.completion_enums import ReasoningEffort
from synthorg.providers.drivers.litellm_kwargs import (
    _AcompletionKwargs,
    _apply_completion_config,
)
from synthorg.providers.models import CompletionConfig

pytestmark = pytest.mark.unit


def _base_kwargs() -> _AcompletionKwargs:
    return {"model": "test-provider/test-basic-001", "messages": []}


class TestApplyCompletionConfig:
    def test_none_config_returns_unmodified_copy(self) -> None:
        """A ``None`` config yields an equal copy and leaves the input alone."""
        kwargs = _base_kwargs()
        result = _apply_completion_config(kwargs, None)

        assert result == kwargs
        assert result is not kwargs

    def test_does_not_mutate_input(self) -> None:
        """The input mapping is never mutated (immutability convention)."""
        kwargs = _base_kwargs()
        _apply_completion_config(kwargs, CompletionConfig(temperature=0.7))

        assert set(kwargs.keys()) == {"model", "messages"}

    def test_applies_set_fields(self) -> None:
        config = CompletionConfig(
            temperature=0.5,
            max_tokens=128,
            stop_sequences=("STOP",),
            timeout=12.0,
        )
        result = _apply_completion_config(_base_kwargs(), config)

        assert result["temperature"] == 0.5
        assert result["max_tokens"] == 128
        assert result["stop"] == ["STOP"]
        assert result["timeout"] == 12.0

    def test_none_valued_fields_not_injected(self) -> None:
        """Fields left at ``None`` are absent (no spurious keys override defaults).

        ``top_p`` is among them: a numeric default would reach the wire on
        every call that never asked for one, stating a truncation the caller
        did not choose and overriding whatever the provider would apply.
        """
        result = _apply_completion_config(
            _base_kwargs(), CompletionConfig(temperature=0.7)
        )

        assert result["temperature"] == 0.7
        assert "top_p" not in result
        assert "max_tokens" not in result
        assert "timeout" not in result
        assert "stop" not in result

    def test_a_stated_top_p_is_still_emitted(self) -> None:
        """The complement: asking for one sends it.

        Omitting an unset threshold is only correct while a stated one still
        reaches the wire; dropping both would silently disable nucleus
        sampling for every prompt class that pins it.
        """
        result = _apply_completion_config(
            _base_kwargs(), CompletionConfig(temperature=0.7, top_p=0.95)
        )

        assert result.get("top_p") == pytest.approx(0.95)

    def test_reasoning_effort_emitted_when_set(self) -> None:
        """A set ``reasoning_effort`` maps to the litellm kwarg as its value."""
        result = _apply_completion_config(
            _base_kwargs(),
            CompletionConfig(reasoning_effort=ReasoningEffort.HIGH),
        )

        assert result["reasoning_effort"] == "high"

    def test_reasoning_effort_absent_by_default(self) -> None:
        result = _apply_completion_config(_base_kwargs(), CompletionConfig())

        assert "reasoning_effort" not in result
