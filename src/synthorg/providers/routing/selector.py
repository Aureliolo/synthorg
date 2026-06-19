"""Model candidate selectors for multi-provider resolution.

When multiple providers serve the same model, a selector picks the
best candidate from the list.  Selectors are synchronous and
constructed with their context (e.g. quota snapshot) already bound.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from synthorg.observability import get_logger
from synthorg.observability.events.routing import (
    ROUTING_CANDIDATE_SELECTED,
    ROUTING_SELECTION_FAILED,
)

from .errors import ModelResolutionError
from .models import ResolvedModel

logger = get_logger(__name__)


def _cost_key(m: ResolvedModel) -> tuple[float, str]:
    """Sort key: cheapest first, then provider name for stable tie-breaking.

    Returns:
        A ``(total_cost_per_1k, provider_name)`` tuple usable as a sort
        key (cheapest first, provider name breaking ties).
    """
    return (m.total_cost_per_1k, m.provider_name)


@runtime_checkable
class ModelCandidateSelector(Protocol):
    """Protocol for selecting among multiple provider candidates."""

    def select(
        self,
        candidates: tuple[ResolvedModel, ...],
    ) -> ResolvedModel:
        """Pick the best candidate from one or more models.

        Args:
            candidates: Non-empty tuple of resolved models for the
                same model reference.

        Returns:
            The selected model.

        Raises:
            ModelResolutionError: If candidates is empty.
        """
        ...


class QuotaAwareSelector:
    """Prefer providers with available quota, then cheapest.

    When quota information is available, candidates from providers
    with remaining quota are preferred.  Among those (or among all
    candidates when none have quota), the cheapest is returned.

    With an empty quota map (the default), all providers are assumed
    available and the selector degrades to cheapest-first.

    The selector is immutable after construction -- reconstruct with
    a fresh quota snapshot to reflect updated quotas.

    Args:
        provider_quota_available: Mapping of provider name to quota
            availability.  Providers absent from the mapping are
            assumed available.
    """

    def __init__(
        self,
        *,
        provider_quota_available: Mapping[str, bool] | None = None,
    ) -> None:
        self._quota: MappingProxyType[str, bool] = MappingProxyType(
            dict(provider_quota_available) if provider_quota_available else {},
        )

    def select(
        self,
        candidates: tuple[ResolvedModel, ...],
    ) -> ResolvedModel:
        """Select the best candidate considering quota and cost.

        Args:
            candidates: Non-empty tuple of resolved models.

        Returns:
            Provider with available quota and lowest cost; if no
            provider has quota, returns the globally cheapest
            candidate.

        Raises:
            ModelResolutionError: If candidates is empty.
        """
        if not candidates:
            msg = "Cannot select from empty candidate list"
            logger.warning(
                ROUTING_SELECTION_FAILED,
                selector="quota_aware",
                reason="empty_candidates",
                error_type=ModelResolutionError.__name__,
            )
            raise ModelResolutionError(msg, context={"selector": "quota_aware"})
        with_quota = [c for c in candidates if self._has_quota(c.provider_name)]
        if not with_quota and len(candidates) > 1 and self._quota:
            logger.warning(
                ROUTING_CANDIDATE_SELECTED,
                reason="all_providers_quota_exhausted",
                candidate_count=len(candidates),
                selector="quota_aware",
            )
        pool: tuple[ResolvedModel, ...] | list[ResolvedModel] = with_quota or candidates
        chosen = min(pool, key=_cost_key)
        if len(candidates) > 1:
            logger.debug(
                ROUTING_CANDIDATE_SELECTED,
                provider=chosen.provider_name,
                model_id=chosen.model_id,
                candidate_count=len(candidates),
                available_count=len(with_quota),
                selector="quota_aware",
            )
        return chosen

    def _has_quota(self, provider_name: str) -> bool:
        """Report whether a provider still has quota (unknown defaults true).

        Returns:
            ``True`` if the provider has quota or is unknown to the
            tracker, ``False`` if it has been marked exhausted.
        """
        return self._quota.get(provider_name, True)


class CheapestSelector:
    """Always pick the cheapest candidate regardless of quota.

    Stateless selector that ignores quota availability and selects
    purely by ``total_cost_per_1k``.  Use when quota enforcement is
    handled externally or when cost minimisation is the sole concern.
    """

    def select(
        self,
        candidates: tuple[ResolvedModel, ...],
    ) -> ResolvedModel:
        """Select the cheapest candidate by total cost per 1k tokens.

        Returns:
            The lowest-cost ``ResolvedModel`` from the candidates.

        Raises:
            ModelResolutionError: If the ``candidates`` tuple is empty.
        """
        if not candidates:
            msg = "Cannot select from empty candidate list"
            logger.warning(
                ROUTING_SELECTION_FAILED,
                selector="cheapest",
                reason="empty_candidates",
                error_type=ModelResolutionError.__name__,
            )
            raise ModelResolutionError(msg, context={"selector": "cheapest"})
        chosen = min(candidates, key=_cost_key)
        if len(candidates) > 1:
            logger.debug(
                ROUTING_CANDIDATE_SELECTED,
                provider=chosen.provider_name,
                model_id=chosen.model_id,
                candidate_count=len(candidates),
                selector="cheapest",
            )
        return chosen
