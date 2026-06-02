"""Tests for the typed LiteLLM ``acompletion`` kwargs assembly."""

import pytest

from synthorg.providers.drivers.litellm_kwargs import (
    _AcompletionKwargs,
    _apply_completion_config,
)
from synthorg.providers.models import CompletionConfig

pytestmark = pytest.mark.unit


def _base_kwargs() -> _AcompletionKwargs:
    return {"model": "test-provider/test-small-001", "messages": []}


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

        ``top_p`` is always present because ``CompletionConfig`` defaults it
        to ``1.0`` (not ``None``); ``temperature`` / ``max_tokens`` /
        ``timeout`` default to ``None`` and must not be injected.
        """
        result = _apply_completion_config(
            _base_kwargs(), CompletionConfig(temperature=0.7)
        )

        assert result["temperature"] == 0.7
        assert result["top_p"] == 1.0
        assert "max_tokens" not in result
        assert "timeout" not in result
        assert "stop" not in result
