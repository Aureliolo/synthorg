"""Model resolver — maps aliases and model IDs to ``ResolvedModel``.

Indexes every model ID and alias to a ``ResolvedModel``.  Typically
built via the ``from_config`` classmethod from
``dict[str, ProviderConfig]``.  Uses ``MappingProxyType`` to guarantee
immutability after construction.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.routing import (
    ROUTING_MODEL_RESOLUTION_FAILED,
    ROUTING_MODEL_RESOLVED,
    ROUTING_RESOLVER_BUILT,
)

from .errors import ModelResolutionError
from .models import ResolvedModel

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig

logger = get_logger(__name__)


class ModelResolver:
    """Resolves model aliases and IDs to ``ResolvedModel`` instances.

    Built from the providers section of the company config.  Each model
    ID and alias is indexed for O(1) lookup.

    Examples:
        Build from config::

            resolver = ModelResolver.from_config(root_config.providers)
            model = resolver.resolve("medium")
    """

    def __init__(
        self,
        index: dict[str, ResolvedModel],
    ) -> None:
        """Initialize with a pre-built ref -> model index.

        Args:
            index: Mapping of model ref to resolved model.  A frozen
                copy is made internally; the caller's dict is not
                modified.
        """
        self._index: MappingProxyType[str, ResolvedModel] = MappingProxyType(
            dict(index),
        )

    @staticmethod
    def _index_ref(
        index: dict[str, ResolvedModel],
        ref: str,
        resolved: ResolvedModel,
        provider_name: str,
    ) -> None:
        """Register a model ref, raising on collision."""
        existing = index.get(ref)
        if existing is not None and existing != resolved:
            logger.error(
                ROUTING_MODEL_RESOLUTION_FAILED,
                ref=ref,
                existing_provider=existing.provider_name,
                existing_model_id=existing.model_id,
                new_provider=provider_name,
                new_model_id=resolved.model_id,
            )
            msg = (
                f"Duplicate model reference {ref!r}: "
                f"{existing.provider_name}/{existing.model_id} "
                f"vs {provider_name}/{resolved.model_id}"
            )
            raise ModelResolutionError(
                msg,
                context={
                    "ref": ref,
                    "existing_provider": existing.provider_name,
                    "new_provider": provider_name,
                },
            )
        index[ref] = resolved

    @classmethod
    def from_config(
        cls,
        providers: dict[str, ProviderConfig],
    ) -> ModelResolver:
        """Build a resolver from a provider config dict.

        Args:
            providers: Provider config dict (key = provider name).

        Returns:
            A new ``ModelResolver`` with all models indexed.
        """
        index: dict[str, ResolvedModel] = {}

        for provider_name, provider_config in providers.items():
            for model_config in provider_config.models:
                resolved = ResolvedModel(
                    provider_name=provider_name,
                    model_id=model_config.id,
                    alias=model_config.alias,
                    cost_per_1k_input=model_config.cost_per_1k_input,
                    cost_per_1k_output=model_config.cost_per_1k_output,
                    max_context=model_config.max_context,
                    estimated_latency_ms=model_config.estimated_latency_ms,
                )
                for ref in (model_config.id, model_config.alias):
                    if ref is None:
                        continue
                    cls._index_ref(index, ref, resolved, provider_name)

        logger.info(
            ROUTING_RESOLVER_BUILT,
            model_count=len({m.model_id for m in index.values()}),
            ref_count=len(index),
            providers=sorted(providers),
        )
        return cls(index)

    def resolve(self, ref: str) -> ResolvedModel:
        """Resolve a model alias or ID to a ``ResolvedModel``.

        Args:
            ref: Model alias or ID string.

        Returns:
            The resolved model.

        Raises:
            ModelResolutionError: If the ref is not found.
        """
        model = self._index.get(ref)
        if model is None:
            logger.warning(
                ROUTING_MODEL_RESOLUTION_FAILED,
                ref=ref,
                available=sorted(self._index),
            )
            msg = f"Model reference {ref!r} not found. Available: {sorted(self._index)}"
            raise ModelResolutionError(msg, context={"ref": ref})
        logger.debug(
            ROUTING_MODEL_RESOLVED,
            ref=ref,
            provider=model.provider_name,
            model_id=model.model_id,
        )
        return model

    def resolve_safe(self, ref: str) -> ResolvedModel | None:
        """Resolve a model ref without raising.

        Returns ``None`` instead of raising ``ModelResolutionError``
        when *ref* is not found.

        Args:
            ref: Model alias or ID string.

        Returns:
            The resolved model, or ``None`` if not found.
        """
        model = self._index.get(ref)
        if model is None:
            logger.debug(
                ROUTING_MODEL_RESOLUTION_FAILED,
                ref=ref,
            )
        return model

    def all_models(self) -> tuple[ResolvedModel, ...]:
        """Return deduplicated tuple of all resolved models."""
        unique = {m.model_id: m for m in self._index.values()}
        return tuple(unique.values())

    def all_models_sorted_by_cost(self) -> tuple[ResolvedModel, ...]:
        """Return models sorted by total cost (ascending).

        Total cost is ``cost_per_1k_input + cost_per_1k_output``.
        """
        return tuple(
            sorted(
                self.all_models(),
                key=lambda m: m.total_cost_per_1k,
            ),
        )

    def all_models_sorted_by_latency(self) -> tuple[ResolvedModel, ...]:
        """Return models sorted by estimated latency (ascending).

        Models with ``None`` latency sort last.
        """
        return tuple(
            sorted(
                self.all_models(),
                key=lambda m: (
                    m.estimated_latency_ms
                    if m.estimated_latency_ms is not None
                    else float("inf")
                ),
            ),
        )
