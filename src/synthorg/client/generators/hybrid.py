"""Hybrid weighted-composition requirement generator."""

import random

from synthorg.client.models import (
    GenerationContext,
    TaskRequirement,
)
from synthorg.client.protocols import RequirementGenerator
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_REQUIREMENT_GENERATED

logger = get_logger(__name__)


class HybridGenerator:
    """Composes multiple generators via weighted random selection.

    For each requirement in the request, picks one delegate
    generator according to the provided weights and calls it with
    ``count=1``. The selected delegate's output is appended to the
    running result; all delegate outputs are flattened into a
    single tuple.
    """

    def __init__(
        self,
        *,
        generators: tuple[tuple[RequirementGenerator, float], ...],
        seed: int | None = None,
    ) -> None:
        """Initialize the hybrid generator.

        Args:
            generators: Tuple of ``(generator, weight)`` pairs.
                Weights must be non-negative and sum to a strictly
                positive total.
            seed: Optional seed for reproducible selection.

        Raises:
            ValueError: If ``generators`` is empty, any weight is
                negative, or the weight sum is not positive.
        """
        if not generators:
            msg = "generators tuple must not be empty"
            raise ValueError(msg)
        for _, weight in generators:
            if weight < 0:
                msg = f"weights must be non-negative, got {weight}"
                raise ValueError(msg)
        total = sum(w for _, w in generators)
        if total <= 0:
            msg = "sum of generator weights must be > 0"
            raise ValueError(msg)
        self._generators = generators
        self._rng = (
            random.Random(seed)  # noqa: S311
            if seed is not None
            else random.Random()  # noqa: S311
        )

    async def generate(
        self,
        context: GenerationContext,
    ) -> tuple[TaskRequirement, ...]:
        """Fan out to weighted delegates and flatten the result.

        Args:
            context: Generation context, passed to each delegate
                with ``count=1``.

        Returns:
            Tuple of up to ``context.count`` requirements; may be
            shorter if some delegates return empty results.
        """
        gens = [g for g, _ in self._generators]
        weights = [w for _, w in self._generators]
        collected: list[TaskRequirement] = []
        for _ in range(context.count):
            selected = self._rng.choices(gens, weights=weights, k=1)[0]
            sub_context = context.model_copy(update={"count": 1})
            produced = await selected.generate(sub_context)
            collected.extend(produced)
        logger.debug(
            CLIENT_REQUIREMENT_GENERATED,
            strategy="hybrid",
            generated=len(collected),
            delegate_count=len(self._generators),
        )
        return tuple(collected)
