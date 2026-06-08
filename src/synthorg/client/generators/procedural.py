"""Procedural algorithmic requirement generator."""

import hashlib
import random
from typing import Final

from synthorg.client.models import (
    GenerationContext,
    TaskRequirement,
)
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.observability import get_logger
from synthorg.observability.events.client import CLIENT_REQUIREMENT_GENERATED

logger = get_logger(__name__)
_DEFAULT_SEED: Final[int] = 42


_TITLE_TEMPLATES: tuple[str, ...] = (
    "Implement {domain} feature {n}",
    "Refactor {domain} module {n}",
    "Add tests for {domain} component {n}",
    "Document {domain} API surface {n}",
    "Fix bug in {domain} subsystem {n}",
    "Investigate {domain} performance issue {n}",
)

_DESCRIPTION_TEMPLATES: tuple[str, ...] = (
    "Design and implement a new feature in the {domain} area that "
    "delivers measurable value to end users.",
    "Refactor existing {domain} code to improve maintainability, "
    "readability, and test coverage.",
    "Add automated test coverage for the {domain} component, "
    "covering happy paths and critical edge cases.",
    "Write developer-facing documentation for the {domain} API, "
    "including examples and a migration guide.",
    "Diagnose and fix a reported bug in the {domain} subsystem "
    "with a regression test to prevent recurrence.",
    "Investigate reported performance issues in {domain} and "
    "propose a plan to address the root cause.",
)

_CRITERIA_POOL: tuple[str, ...] = (
    "Unit tests pass",
    "Integration tests pass",
    "Documentation updated",
    "Code review approved",
    "CI pipeline green",
    "Performance benchmarks meet target",
    "No regressions in existing tests",
    "Security review complete",
)

_TASK_TYPES: tuple[TaskType, ...] = tuple(TaskType)
_PRIORITIES: tuple[Priority, ...] = tuple(Priority)


class ProceduralGenerator:
    """Deterministic algorithmic requirement generator.

    Combines a configurable seed with the generation context
    (``project_id``, ``domain``, ``count``) via a stable BLAKE2
    digest to produce a per-call random seed. Same inputs yield the
    same outputs across processes and machines, which keeps tests
    and fuzzing runs reproducible.
    """

    def __init__(self, *, seed: int = _DEFAULT_SEED) -> None:
        """Initialize the procedural generator.

        Args:
            seed: Base seed combined with context per call.
        """
        self._seed = seed

    async def generate(
        self,
        context: GenerationContext,
    ) -> tuple[TaskRequirement, ...]:
        """Generate procedural requirements from the context.

        Args:
            context: Generation context driving templates and
                complexity choices.

        Returns:
            Tuple of ``context.count`` requirements.
        """
        rng = self._rng_for(context)
        allowed = tuple(context.complexity_range)
        requirements: list[TaskRequirement] = [
            self._build_requirement(rng, context, allowed, index)
            for index in range(context.count)
        ]
        logger.debug(
            CLIENT_REQUIREMENT_GENERATED,
            strategy="procedural",
            generated=len(requirements),
            domain=context.domain,
        )
        return tuple(requirements)

    def _rng_for(self, context: GenerationContext) -> random.Random:
        seed_str = f"{self._seed}|{context.project_id}|{context.domain}|{context.count}"
        digest = hashlib.blake2b(
            seed_str.encode("utf-8"),
            digest_size=8,
        ).digest()
        return random.Random(int.from_bytes(digest, "big"))  # noqa: S311

    @staticmethod
    def _build_requirement(
        rng: random.Random,
        context: GenerationContext,
        allowed: tuple[Complexity, ...],
        index: int,
    ) -> TaskRequirement:
        title_tpl = rng.choice(_TITLE_TEMPLATES)
        desc_tpl = rng.choice(_DESCRIPTION_TEMPLATES)
        title = title_tpl.format(domain=context.domain, n=index + 1)
        description = desc_tpl.format(domain=context.domain)
        num_criteria = rng.randint(1, 4)
        criteria = tuple(rng.sample(_CRITERIA_POOL, num_criteria))
        return TaskRequirement(
            title=title,
            description=description,
            task_type=rng.choice(_TASK_TYPES),
            priority=rng.choice(_PRIORITIES),
            estimated_complexity=rng.choice(allowed),
            acceptance_criteria=criteria,
        )
