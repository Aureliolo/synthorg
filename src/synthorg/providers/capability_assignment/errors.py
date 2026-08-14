# module-kind: code
"""Errors for the capability-assignment subsystem."""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class CapabilityClassifierModelUnsetError(ConflictError):
    """Raised when an LLM capability recommendation is requested but no model is set.

    The LLM recommender runs on the operator-selected
    ``providers.capability_classifier_model``. When it is unset the recommend action
    cannot run, so the caller is told to pick a classifier model first (409).
    """

    default_message: ClassVar[str] = (
        "No capability-classifier model is configured. Set "
        "providers.capability_classifier_model (a provider + model) before using the "
        "LLM capability recommender."
    )


class CapabilityClassifierDisabledError(ConflictError):
    """Raised when the LLM recommender is invoked while opt-in is off.

    The recommender is off by default (``providers.capability_classifier_enabled``);
    it spends tokens, so an operator opts in explicitly before it runs (409).
    """

    default_message: ClassVar[str] = (
        "The LLM capability recommender is disabled. Enable "
        "providers.capability_classifier_enabled before requesting a recommendation."
    )


class CapabilityClassifierProviderUnavailableError(ConflictError):
    """Raised when the classifier model's provider is not registered.

    A classifier model is configured, but the provider it names is not in the
    live registry (e.g. it was deregistered after selection), so the recommender
    cannot be built. Distinguished from the model-unset case so the operator
    fixes the provider rather than re-picking a model (409).
    """

    default_message: ClassVar[str] = (
        "The configured capability-classifier model names a provider that is not "
        "registered. Re-add the provider or pick a classifier model on a "
        "registered provider."
    )


class CapabilityOverrideStoreReadOnlyError(ConflictError):
    """Raised when a capability override is persisted through a read-only store.

    The override store is read-only when built without a settings service
    (e.g. before persistence is wired), so an override cannot be saved (409).
    """

    default_message: ClassVar[str] = (
        "The capability-override store is read-only (no settings service is wired), "
        "so the override cannot be persisted."
    )


__all__ = [
    "CapabilityClassifierDisabledError",
    "CapabilityClassifierModelUnsetError",
    "CapabilityClassifierProviderUnavailableError",
    "CapabilityOverrideStoreReadOnlyError",
]
