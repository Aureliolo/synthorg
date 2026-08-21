"""Tests for provider family lookup."""

from unittest.mock import MagicMock

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.family import get_family, shares_lineage


def _mock_config(family: str | None = None) -> MagicMock:
    config = MagicMock()
    config.family = family
    return config


@pytest.mark.unit
def test_get_family_returns_explicit_family() -> None:
    configs = {"prov-a": _mock_config(family="family-a")}
    assert get_family("prov-a", configs) == "family-a"


@pytest.mark.unit
def test_get_family_falls_back_to_provider_name() -> None:
    """An unfamilied provider is its own family, so it collides only with itself."""
    configs = {"prov-a": _mock_config(family=None)}
    assert get_family("prov-a", configs) == "prov-a"


@pytest.mark.unit
def test_get_family_unknown_provider_returns_name() -> None:
    assert get_family("unknown", {}) == "unknown"


def _aggregator(*models: tuple[str, str | None]) -> dict[str, ProviderConfig]:
    """One connection serving several organisations, as an aggregator does.

    Args:
        models: ``(model_id, family)`` pairs reached through it; a ``None``
            family is a model the config does not classify.

    Returns:
        A config map under a single provider name.
    """
    return {
        "aggregator": ProviderConfig(
            connection_name=NotBlankStr("aggregator-connection"),
            models=tuple(
                ProviderModelConfig(
                    id=NotBlankStr(model_id),
                    metadata=ModelMetadata(family=family),
                )
                for model_id, family in models
            ),
        )
    }


class TestOneConnectionCanServeManyFamilies:
    """The aggregator case, which a provider-keyed lookup cannot express."""

    @pytest.mark.unit
    def test_the_model_decides_the_family_not_the_connection(self) -> None:
        configs = _aggregator(("kimi-k3", "kimi"), ("deepseek-v4-pro", "deepseek"))

        assert get_family("aggregator", configs, "kimi-k3") == "kimi"
        assert get_family("aggregator", configs, "deepseek-v4-pro") == "deepseek"

    @pytest.mark.unit
    def test_a_model_with_no_declared_family_falls_back_to_the_connection(self) -> None:
        configs = _aggregator(("mystery-1", None))

        assert get_family("aggregator", configs, "mystery-1") == "aggregator"


class TestLineageIsComparedOnTheBaseFamily:
    """A variant names what a model was tuned for, not who trained it."""

    @pytest.mark.unit
    def test_a_code_variant_does_not_independently_judge_its_own_lineage(self) -> None:
        # The false decorrelation: both are the same organisation, and reading
        # them as different families claims an independence nobody has.
        assert shares_lineage("kimi-k-code", "kimi-k") is True

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("qwen-coder", "qwen"),
            ("qwen-coder", "qwen-chat"),
            ("deepseek-reasoning", "deepseek"),
        ],
    )
    def test_variants_of_one_base_share_lineage(self, left: str, right: str) -> None:
        assert shares_lineage(left, right) is True

    @pytest.mark.unit
    def test_genuinely_different_organisations_do_not(self) -> None:
        assert shares_lineage("kimi-k-code", "deepseek-v") is False

    @pytest.mark.unit
    def test_a_family_whose_name_merely_ends_in_a_word_is_not_split(self) -> None:
        # Split on the separator, never on a substring: a family that happens
        # to end in these letters is one name, not a base plus a variant.
        assert shares_lineage("nemotron", "nemo") is False
