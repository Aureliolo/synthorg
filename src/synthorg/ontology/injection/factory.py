"""Factory for ontology injection strategy creation.

Maps ``InjectionStrategy`` config enum values to concrete
strategy implementations.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_INJECTION_UNKNOWN_STRATEGY,
)
from synthorg.ontology.config import InjectionStrategy, OntologyInjectionConfig
from synthorg.ontology.injection.hybrid import HybridInjectionStrategy
from synthorg.ontology.injection.memory import MemoryBasedInjectionStrategy
from synthorg.ontology.injection.prompt import PromptInjectionStrategy
from synthorg.ontology.injection.tool import ToolBasedInjectionStrategy

if TYPE_CHECKING:
    from synthorg.memory.injection import TokenEstimator
    from synthorg.ontology.injection.protocol import OntologyInjectionStrategy
    from synthorg.persistence.ontology_protocol import OntologyEntityRepository

logger = get_logger(__name__)


def create_injection_strategy(
    config: OntologyInjectionConfig,
    backend: OntologyEntityRepository,
    *,
    token_estimator: TokenEstimator | None = None,
) -> OntologyInjectionStrategy:
    """Create an injection strategy from configuration.

    Args:
        config: Injection configuration with strategy selection.
        backend: Ontology backend for entity retrieval.
        token_estimator: Optional token estimator override.

    Returns:
        Concrete ``OntologyInjectionStrategy`` implementation.

    Raises:
        ValueError: If the strategy value is unrecognised.
    """
    strategy = config.strategy

    if strategy == InjectionStrategy.PROMPT:
        return PromptInjectionStrategy(
            backend=backend,
            core_token_budget=config.core_token_budget,
            token_estimator=token_estimator,
        )

    if strategy == InjectionStrategy.TOOL:
        return ToolBasedInjectionStrategy(
            backend=backend,
            tool_name=config.tool_name,
        )

    if strategy == InjectionStrategy.HYBRID:
        return HybridInjectionStrategy(
            backend=backend,
            core_token_budget=config.core_token_budget,
            tool_name=config.tool_name,
            token_estimator=token_estimator,
        )

    if strategy == InjectionStrategy.MEMORY:
        return MemoryBasedInjectionStrategy()

    msg = f"Unknown injection strategy: {strategy!r}"  # type: ignore[unreachable]
    logger.error(
        ONTOLOGY_INJECTION_UNKNOWN_STRATEGY,
        strategy=str(strategy),
    )
    raise ValueError(msg)
