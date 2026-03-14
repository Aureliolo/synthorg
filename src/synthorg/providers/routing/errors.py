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


class UnknownStrategyError(RoutingError):
    """Configured strategy name is not recognized."""

    is_retryable = False
