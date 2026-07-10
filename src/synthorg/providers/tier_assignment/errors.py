# module-kind: code
"""Errors for the tier-assignment subsystem."""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class TierClassifierModelUnsetError(ConflictError):
    """Raised when an LLM tier recommendation is requested but no model is set.

    The LLM recommender runs on the operator-selected
    ``providers.tier_classifier_model``. When it is unset the recommend action
    cannot run, so the operator is asked to pick a classifier model first (a
    409 the dashboard routes to the classifier-model picker).
    """

    default_message: ClassVar[str] = (
        "No tier-classifier model is configured. Set "
        "providers.tier_classifier_model (a provider + model) before using the "
        "LLM tier recommender."
    )


__all__ = ["TierClassifierModelUnsetError"]
