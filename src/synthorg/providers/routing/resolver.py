"""Model resolver -- maps aliases and model IDs to ``ResolvedModel``.

Indexes every model ID and alias to one or more ``ResolvedModel``
instances (multi-provider support).  When multiple providers serve
the same model, a ``ModelCandidateSelector`` picks the best
candidate at resolution time.  Use ``resolve_all()`` to retrieve
all provider variants for a ref without triggering selection.

Typically built via the ``from_config`` classmethod from
``dict[str, ProviderConfig]``.  Uses ``MappingProxyType`` to guarantee
immutability after construction.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.routing import (
    ROUTING_MODEL_RESOLUTION_FAILED,
    ROUTING_MODEL_RESOLVED,
    ROUTING_MULTI_PROVIDER_REGISTERED,
    ROUTING_RESOLVER_BUILT,
    ROUTING_SELECTION_FAILED,
)

from .errors import ModelResolutionError
from .models import ResolvedModel
from .selector import ModelCandidateSelector, QuotaAwareSelector

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig

logger = get_logger(__name__)


class ModelResolver:
    """Resolves model aliases and IDs to ``ResolvedModel`` instances.

    Built from the providers section of the company config.  Each model
    ID and alias is indexed for O(1) lookup.  When multiple providers
    register the same ref, the injected ``selector`` picks the best
    candidate.

    Examples:
        Build from config::

            resolver = ModelResolver.from_config(root_config.providers)
            model = resolver.resolve("medium")
    """

    def __init__(
        self,
        index: dict[str, tuple[ResolvedModel, ...]],
        *,
        selector: ModelCandidateSelector | None = None,
    ) -> None:
        """Initialize with a pre-built ref -> candidates index.

        Args:
            index: Mapping of model ref to tuple of resolved models.
                A frozen copy is made internally; the caller's dict
                is not modified.  Every tuple must be non-empty.
            selector: Strategy for picking among multiple candidates.
                Defaults to ``QuotaAwareSelector()`` (prefers providers
                with available quota, then cheapest).

        Raises:
            ValueError: If any candidate tuple is empty.
        """
        validated: dict[str, tuple[ResolvedModel, ...]] = {}
        for k, v in index.items():
            t = tuple(v)
            if not t:
                msg = f"Empty candidate list for ref {k!r}"
                raise ValueError(msg)
            validated[k] = t

        self._index: MappingProxyType[str, tuple[ResolvedModel, ...]] = (
            MappingProxyType(validated)
        )
        self._selector: ModelCandidateSelector = (
            selector if selector is not None else QuotaAwareSelector()
        )

    @property
    def selector(self) -> ModelCandidateSelector:
        """The active candidate selector."""
        return self._selector

    @staticmethod
    def _index_ref(
        index: dict[str, list[ResolvedModel]],
        ref: str,
        resolved: ResolvedModel,
        provider_name: str,
    ) -> None:
        """Register a model ref; appends on distinct overlap, skips exact duplicates."""
        existing_list = index.get(ref)
        if existing_list is not None:
            for existing in existing_list:
                if existing == resolved:
                    logger.debug(
                        ROUTING_MULTI_PROVIDER_REGISTERED,
                        ref=ref,
                        provider=provider_name,
                        reason="exact_duplicate_skipped",
                    )
                    return
            logger.info(
                ROUTING_MULTI_PROVIDER_REGISTERED,
                ref=ref,
                existing_providers=[e.provider_name for e in existing_list],
                new_provider=provider_name,
                new_model_id=resolved.model_id,
            )
            existing_list.append(resolved)
        else:
            index[ref] = [resolved]

    @classmethod
    def from_config(
        cls,
        providers: dict[str, ProviderConfig],
        *,
        selector: ModelCandidateSelector | None = None,
    ) -> ModelResolver:
        """Build a resolver from a provider config dict.

        Args:
            providers: Provider config dict (key = provider name).
            selector: Optional candidate selector override.

        Returns:
            A new ``ModelResolver`` with all models indexed.
        """
        index: dict[str, list[ResolvedModel]] = {}

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

        tuple_index = {k: tuple(v) for k, v in index.items()}
        multi_refs = [k for k, v in tuple_index.items() if len(v) > 1]

        logger.info(
            ROUTING_RESOLVER_BUILT,
            model_count=len(
                {
                    (m.provider_name, m.model_id)
                    for candidates in tuple_index.values()
                    for m in candidates
                },
            ),
            ref_count=len(tuple_index),
            providers=sorted(providers),
            multi_provider_refs=multi_refs,
        )
        return cls(tuple_index, selector=selector)

    def resolve(self, ref: str) -> ResolvedModel:
        """Resolve a model alias or ID to a ``ResolvedModel``.

        When multiple providers serve the same model, the selector
        picks the best candidate.

        Args:
            ref: Model alias or ID string.

        Returns:
            The resolved model.

        Raises:
            ModelResolutionError: If the ref is not found or the
                selector fails.
        """
        candidates = self._index.get(ref)
        if candidates is None:
            available = sorted(self._index)
            logger.warning(
                ROUTING_MODEL_RESOLUTION_FAILED,
                ref=ref,
                available=available,
            )
            msg = f"Model reference {ref!r} not found. Available: {available}"
            raise ModelResolutionError(msg, context={"ref": ref})
        try:
            model = self._selector.select(candidates)
        except ModelResolutionError as exc:
            logger.warning(
                ROUTING_SELECTION_FAILED,
                ref=ref,
                candidate_count=len(candidates),
                selector=type(self._selector).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                ROUTING_SELECTION_FAILED,
                ref=ref,
                candidate_count=len(candidates),
                selector=type(self._selector).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Selector failed for {ref!r} with {len(candidates)} candidates: {safe_error_description(exc)}"  # noqa: E501
            raise ModelResolutionError(msg, context={"ref": ref}) from exc
        logger.debug(
            ROUTING_MODEL_RESOLVED,
            ref=ref,
            provider=model.provider_name,
            model_id=model.model_id,
            candidate_count=len(candidates),
        )
        return model

    def resolve_safe(self, ref: str) -> ResolvedModel | None:
        """Resolve a model ref without raising.

        Returns ``None`` instead of raising ``ModelResolutionError``
        when *ref* is not found or the selector fails.

        Args:
            ref: Model alias or ID string.

        Returns:
            The resolved model, or ``None`` if not found.
        """
        candidates = self._index.get(ref)
        if candidates is None:
            logger.debug(
                ROUTING_MODEL_RESOLUTION_FAILED,
                ref=ref,
            )
            return None
        try:
            return self._selector.select(candidates)
        except ModelResolutionError as exc:
            logger.debug(
                ROUTING_SELECTION_FAILED,
                ref=ref,
                candidate_count=len(candidates),
                selector=type(self._selector).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="selector_raised_resolution_error",
            )
            return None
        except Exception as exc:
            reraise_critical(exc)
            # Defensive fallback: any non-domain selector error converts
            # to a "no model found" outcome rather than propagating up
            # the model-resolution stack, where the caller would have
            # to plumb a second failure mode through every routing /
            # provider-management code path. The WARNING below (with
            # ``reason="unexpected_selector_error"``) is loud enough
            # for operators to see the underlying cause; ``MemoryError``
            # and ``RecursionError`` are re-raised via
            # :func:`reraise_critical` so interpreter-state failures
            # are not silenced here.
            logger.warning(
                ROUTING_SELECTION_FAILED,
                ref=ref,
                candidate_count=len(candidates),
                selector=type(self._selector).__name__,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="unexpected_selector_error",
            )
            return None

    def resolve_all(self, ref: str) -> tuple[ResolvedModel, ...]:
        """Return all provider variants for a model ref.

        Args:
            ref: Model alias or ID string.

        Returns:
            Tuple of all candidates, or empty tuple if not found.
        """
        return self._index.get(ref, ())

    def all_models(self) -> tuple[ResolvedModel, ...]:
        """Return all resolved models including multi-provider variants.

        Deduplication is by ``(provider_name, model_id)`` pair, so the
        same model from different providers appears as separate entries.
        """
        seen: set[tuple[str, str]] = set()
        result: list[ResolvedModel] = []
        for candidates in self._index.values():
            for m in candidates:
                key = (m.provider_name, m.model_id)
                if key not in seen:
                    seen.add(key)
                    result.append(m)
        return tuple(result)

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
