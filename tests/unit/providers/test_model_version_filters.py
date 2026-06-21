"""Tests for the ``MODEL_VERSION_FILTERS`` generation allowlists.

The Anthropic filter must keep the >=4.5 floor while matching
multi-digit minor versions (4.10, 4.11, ...); the old ``4-[56789]``
character class silently dropped every model from 4.10 onward.
"""

import pytest

from synthorg.providers.presets import MODEL_VERSION_FILTERS

pytestmark = pytest.mark.unit


class TestAnthropicVersionFilter:
    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-sonnet-4-5-20251001",
            "claude-haiku-4-5",
            "claude-opus-4-9",
            "claude-sonnet-4-10",
            "claude-sonnet-4-11",
            "claude-opus-4-20",
        ],
    )
    def test_matches_supported_minors(self, model_id: str) -> None:
        assert MODEL_VERSION_FILTERS["anthropic"].search(model_id)

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-sonnet-4-4",
            "claude-haiku-4-0",
            "claude-opus-3-5",
            "claude-sonnet-3-7",
        ],
    )
    def test_rejects_below_floor(self, model_id: str) -> None:
        assert MODEL_VERSION_FILTERS["anthropic"].search(model_id) is None
