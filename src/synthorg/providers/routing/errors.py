"""Routing error hierarchy.

All routing errors extend ``ProviderError`` so the entire provider
layer shares a single exception tree.
"""

from synthorg.providers.errors import ProviderError


class RoutingError(ProviderError):
    """Base exception for all model-routing errors."""

    is_retryable = False


class ModelResolutionError(RoutingError):
    """Model alias or ID could not be found in any provider."""

    is_retryable = False


class NoAvailableModelError(RoutingError):
    """All candidate models exhausted (primary + fallbacks)."""

    is_retryable = False


class UnknownRoutingStrategyError(RoutingError):
    """Configured routing strategy name is not recognized.

    Distinct from :class:`synthorg.client.factory.UnknownStrategyError`
    (a validation error raised when a client-factory discriminator does
    not map to any registered strategy). This error fires at runtime
    when the router resolves a configured strategy name that is not in
    :data:`STRATEGY_MAP`; it propagates as a 502 PROVIDER_ERROR because
    routing is part of the provider surface.
    """

    is_retryable = False
