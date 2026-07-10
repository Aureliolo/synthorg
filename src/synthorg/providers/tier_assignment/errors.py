# module-kind: code
"""Errors for the tier-assignment subsystem."""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class TierClassifierModelUnsetError(ConflictError):
    """Raised when an LLM tier recommendation is requested but no model is set.

    The LLM recommender runs on the operator-selected
    ``providers.tier_classifier_model``. When it is unset the recommend action
    cannot run, so the caller is told to pick a classifier model first (409).
    """

    default_message: ClassVar[str] = (
        "No tier-classifier model is configured. Set "
        "providers.tier_classifier_model (a provider + model) before using the "
        "LLM tier recommender."
    )


class TierClassifierDisabledError(ConflictError):
    """Raised when the LLM recommender is invoked while opt-in is off.

    The recommender is off by default (``providers.tier_classifier_enabled``);
    it spends tokens, so an operator opts in explicitly before it runs (409).
    """

    default_message: ClassVar[str] = (
        "The LLM tier recommender is disabled. Enable "
        "providers.tier_classifier_enabled before requesting a recommendation."
    )


class TierClassifierProviderUnavailableError(ConflictError):
    """Raised when the classifier model's provider is not registered.

    A classifier model is configured, but the provider it names is not in the
    live registry (e.g. it was deregistered after selection), so the recommender
    cannot be built. Distinguished from the model-unset case so the operator
    fixes the provider rather than re-picking a model (409).
    """

    default_message: ClassVar[str] = (
        "The configured tier-classifier model names a provider that is not "
        "registered. Re-add the provider or pick a classifier model on a "
        "registered provider."
    )


class TierOverrideStoreReadOnlyError(ConflictError):
    """Raised when a tier override is persisted through a read-only store.

    The override store is read-only when built without a settings service
    (e.g. before persistence is wired), so an override cannot be saved (409).
    """

    default_message: ClassVar[str] = (
        "The tier-override store is read-only (no settings service is wired), "
        "so the override cannot be persisted."
    )


__all__ = [
    "TierClassifierDisabledError",
    "TierClassifierModelUnsetError",
    "TierClassifierProviderUnavailableError",
    "TierOverrideStoreReadOnlyError",
]
