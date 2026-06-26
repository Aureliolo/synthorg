"""Tests for ProviderModelDefaults config: values, bounds, and immutability."""

import pytest
from pydantic import ValidationError

from synthorg.providers.defaults_config import ProviderModelDefaults

pytestmark = pytest.mark.unit


class TestProviderModelDefaults:
    """Default fallback values and bounds."""

    def test_default_fallback_is_4096(self) -> None:
        # 4096 is the conservative default applied when the upstream provider
        # API reports no context ceiling.
        assert ProviderModelDefaults().fallback_max_output_tokens == 4096

    def test_frozen(self) -> None:
        cfg = ProviderModelDefaults()
        with pytest.raises(ValidationError):
            cfg.fallback_max_output_tokens = 8192  # type: ignore[misc]

    @pytest.mark.parametrize(
        "value",
        [0, -1, 32_769],
        ids=["zero", "negative", "above_ceiling"],
    )
    def test_invalid_fallback_max_output_tokens(self, value: int) -> None:
        with pytest.raises(ValidationError, match="fallback_max_output_tokens"):
            ProviderModelDefaults(fallback_max_output_tokens=value)

    def test_at_ceiling_accepted(self) -> None:
        cfg = ProviderModelDefaults(fallback_max_output_tokens=32_768)
        assert cfg.fallback_max_output_tokens == 32_768

    def test_custom_value_accepted(self) -> None:
        # Operators running long-context models can raise the fallback
        # without editing source.
        cfg = ProviderModelDefaults(fallback_max_output_tokens=16_384)
        assert cfg.fallback_max_output_tokens == 16_384
