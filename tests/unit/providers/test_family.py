"""Tests for provider family lookup."""

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.family import get_family, shares_lineage

pytestmark = pytest.mark.unit


def _config(family: str | None = None) -> ProviderConfig:
    """Build a connection declaring *family* and nothing else.

    A real config rather than a stand-in: every other field has a default, so
    the typed object costs no more than a double and cannot answer a
    truthy placeholder where the code expects a family string or ``None``.

    Returns:
        The connection.
    """
    return ProviderConfig(connection_name=NotBlankStr("connection"), family=family)


def test_get_family_returns_explicit_family() -> None:
    configs = {"prov-a": _config(family="family-a")}
    assert get_family("prov-a", configs) == "family-a"


def test_get_family_falls_back_to_provider_name() -> None:
    """An unfamilied provider is its own family, so it collides only with itself."""
    configs = {"prov-a": _config(family=None)}
    assert get_family("prov-a", configs) == "prov-a"


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

    def test_the_model_decides_the_family_not_the_connection(self) -> None:
        configs = _aggregator(
            ("example-capable-001", "family-a"),
            ("example-expert-001", "family-b"),
        )

        assert get_family("aggregator", configs, "example-capable-001") == "family-a"
        assert get_family("aggregator", configs, "example-expert-001") == "family-b"

    def test_a_model_with_no_declared_family_falls_back_to_the_connection(self) -> None:
        configs = _aggregator(("mystery-1", None))

        assert get_family("aggregator", configs, "mystery-1") == "aggregator"


class TestLineageIsComparedOnTheBaseFamily:
    """A variant names what a model was tuned for, not who trained it."""

    def test_a_code_variant_does_not_independently_judge_its_own_lineage(self) -> None:
        # The false decorrelation: both are the same organisation, and reading
        # them as different families claims an independence nobody has.
        assert shares_lineage("family-a-code", "family-a") is True

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("family-a-coder", "family-a"),
            ("family-a-coder", "family-a-chat"),
            ("family-b-reasoning", "family-b"),
        ],
    )
    def test_variants_of_one_base_share_lineage(self, left: str, right: str) -> None:
        assert shares_lineage(left, right) is True

    def test_genuinely_different_organisations_do_not(self) -> None:
        assert shares_lineage("family-a-code", "family-b-vision") is False

    def test_a_family_whose_name_merely_ends_in_a_word_is_not_split(self) -> None:
        # Split on the separator, never on a substring: a family that happens
        # to end in these letters is one name, not a base plus a variant.
        assert shares_lineage("familycode", "family") is False
