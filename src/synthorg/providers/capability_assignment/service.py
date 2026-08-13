# module-kind: service
"""Compose heuristic classification with persisted overrides into a tier map.

The effective tier of a configured model is its deterministic heuristic
classification, overlaid by an operator / LLM-accepted override when one is
persisted. The heuristic layer is recomputed from live capability metadata on
every read, so only overrides are stored.
"""

from collections.abc import Mapping
from typing import Final, Protocol, runtime_checkable

from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import CapabilityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_TIER_CLASSIFIED,
    PROVIDER_TIER_OVERRIDDEN,
)
from synthorg.providers.capability_assignment.classifier import (
    HeuristicTierClassifier,
    ModelCapabilityClassifier,
)
from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityOverride,
    CapabilityOverrideMap,
    OverrideProvenance,
)

logger = get_logger(__name__)

#: An override is authoritative over the heuristic, so it carries full trust.
_OVERRIDE_CONFIDENCE: Final[float] = 1.0


@runtime_checkable
class TierOverrideStore(Protocol):
    """Loads and persists the tier-override envelope."""

    async def load(self) -> CapabilityOverrideMap:
        """Return the persisted override map (empty when none is stored)."""
        ...

    async def save(self, overrides: CapabilityOverrideMap) -> None:
        """Persist *overrides*."""
        ...


class CapabilityAssignmentService:
    """Builds the effective per-model tier map and applies overrides.

    Args:
        store: Persistence for the override envelope.
        classifier: The heuristic classifier for the un-overridden layer.
        clock: Time source stamped onto written overrides.
    """

    __slots__ = ("_classifier", "_clock", "_store")

    def __init__(
        self,
        *,
        store: TierOverrideStore,
        classifier: ModelCapabilityClassifier | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._classifier = classifier or HeuristicTierClassifier()
        self._clock = clock or SystemClock()

    async def effective_assignments(
        self,
        providers: Mapping[str, ProviderConfig],
    ) -> tuple[CapabilityAssignment, ...]:
        """Return the effective tier assignment for every configured model.

        Each model's heuristic classification is overlaid by a persisted
        override when one exists.

        Returns:
            One :class:`CapabilityAssignment` per model across all providers, ordered
            by ``(provider, model_id)``.
        """
        override_index = await self._override_index()
        assignments: list[CapabilityAssignment] = []
        for provider_name in sorted(providers):
            config = providers[provider_name]
            for model in sorted(config.models, key=lambda m: m.id):
                override = override_index.get((provider_name, model.id))
                if override is not None:
                    assignments.append(
                        CapabilityAssignment(
                            provider=provider_name,
                            model_id=model.id,
                            capability=override.capability,
                            provenance=override.provenance,
                            confidence=_OVERRIDE_CONFIDENCE,
                            reason=override.reason,
                        ),
                    )
                    continue
                classification = self._classifier.classify(model)
                assignments.append(
                    CapabilityAssignment(
                        provider=provider_name,
                        model_id=model.id,
                        capability=classification.capability,
                        provenance="heuristic",
                        confidence=classification.confidence,
                        reason=classification.reason,
                    ),
                )
        return tuple(assignments)

    async def capability_lookup(
        self,
        providers: Mapping[str, ProviderConfig],
    ) -> dict[tuple[str, str], CapabilityLevel]:
        """Return a ``(provider, model_id) -> rung`` map for the resolver.

        Returns:
            The effective rung of each configured model, keyed by its
            ``(provider, model_id)`` pair.
        """
        assignments = await self.effective_assignments(providers)
        lookup: dict[tuple[str, str], CapabilityLevel] = {
            (str(a.provider), str(a.model_id)): a.capability for a in assignments
        }
        logger.info(
            PROVIDER_TIER_CLASSIFIED,
            model_count=len(lookup),
            provider_count=len(providers),
        )
        return lookup

    async def set_override(
        self,
        *,
        provider: str,
        model_id: str,
        capability: CapabilityLevel,
        provenance: OverrideProvenance,
        reason: str,
    ) -> CapabilityOverride:
        """Persist an override for one model, replacing any prior override.

        Returns:
            The written :class:`CapabilityOverride`.
        """
        override = CapabilityOverride(
            provider=provider,
            model_id=model_id,
            capability=capability,
            provenance=provenance,
            reason=reason,
            updated_at=self._clock.now(),
        )
        current = await self._store.load()
        kept = tuple(
            o
            for o in current.overrides
            if (o.provider, o.model_id) != (provider, model_id)
        )
        await self._store.save(
            current.model_copy(update={"overrides": (*kept, override)}),
        )
        logger.info(
            PROVIDER_TIER_OVERRIDDEN,
            provider=provider,
            model_id=model_id,
            capability=capability,
            provenance=provenance,
            action="set",
        )
        return override

    async def clear_override(self, *, provider: str, model_id: str) -> bool:
        """Remove the override for one model, reverting it to the heuristic.

        Returns:
            ``True`` when an override was removed, ``False`` when none existed.
        """
        current = await self._store.load()
        kept = tuple(
            o
            for o in current.overrides
            if (o.provider, o.model_id) != (provider, model_id)
        )
        if len(kept) == len(current.overrides):
            return False
        await self._store.save(current.model_copy(update={"overrides": kept}))
        logger.info(
            PROVIDER_TIER_OVERRIDDEN,
            provider=provider,
            model_id=model_id,
            action="clear",
        )
        return True

    async def _override_index(
        self,
    ) -> dict[tuple[str, str], CapabilityOverride]:
        """Return the persisted overrides keyed by ``(provider, model_id)``.

        Returns:
            The override index; empty when nothing is persisted.
        """
        stored = await self._store.load()
        return {(o.provider, o.model_id): o for o in stored.overrides}


__all__ = ["CapabilityAssignmentService", "TierOverrideStore"]
