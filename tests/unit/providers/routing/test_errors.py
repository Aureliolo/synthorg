"""Tests for routing error hierarchy."""

import pytest

from synthorg.providers.errors import ProviderError
from synthorg.providers.routing.errors import (
    ModelResolutionError,
    NoAvailableModelError,
    RoutingError,
    UnknownStrategyError,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestRoutingErrorHierarchy:
    """All routing errors extend ProviderError via RoutingError."""

    def test_routing_error_is_provider_error(self) -> None:
        assert issubclass(RoutingError, ProviderError)

    @pytest.mark.parametrize(
        "cls",
        [
            ModelResolutionError,
            NoAvailableModelError,
            UnknownStrategyError,
        ],
    )
    def test_subclasses_extend_routing_error(
        self,
        cls: type[RoutingError],
    ) -> None:
        assert issubclass(cls, RoutingError)
        assert issubclass(cls, ProviderError)

    @pytest.mark.parametrize(
        "cls",
        [
            RoutingError,
            ModelResolutionError,
            NoAvailableModelError,
            UnknownStrategyError,
        ],
    )
    def test_all_not_retryable(
        self,
        cls: type[RoutingError],
    ) -> None:
        err = cls("test")
        assert err.is_retryable is False

    def test_catchable_as_provider_error(self) -> None:
        with pytest.raises(ProviderError):
            raise ModelResolutionError("not found")  # noqa: EM101, TRY003

    def test_message_and_context(self) -> None:
        err = RoutingError("oops", context={"ref": "foo"})
        assert err.message == "oops"
        assert err.context["ref"] == "foo"
        assert "ref='foo'" in str(err)
