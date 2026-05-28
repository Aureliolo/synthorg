"""Template-based requirement generator."""

import json
import random
from pathlib import Path

from pydantic import JsonValue, ValidationError

from synthorg.client.models import (
    GenerationContext,
    TaskRequirement,
)
from synthorg.core.enums import Complexity, Priority, TaskType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.client import CLIENT_REQUIREMENT_GENERATED

logger = get_logger(__name__)


class TemplateGenerator:
    """Loads requirements from a JSON template bundle.

    The bundle is a JSON array of objects with the following fields:

    - ``title``, ``description`` (required)
    - ``task_type``, ``priority``, ``estimated_complexity`` (optional
      enum strings)
    - ``acceptance_criteria`` (optional list of strings)
    - ``domain`` (optional filter matched against
      ``GenerationContext.domain``)
    - ``complexity`` (optional filter matched against
      ``GenerationContext.complexity_range``)

    Sampling is reproducible when a ``seed`` is supplied.
    """

    def __init__(
        self,
        *,
        template_path: Path | str,
        seed: int | None = None,
    ) -> None:
        """Initialize the template generator.

        Args:
            template_path: Path to a JSON file containing a template
                array.
            seed: Optional seed for reproducible sampling.

        Raises:
            FileNotFoundError: If ``template_path`` does not exist.
            TypeError: If the file content is not a JSON array.
        """
        path = Path(template_path)
        if not path.exists():
            msg = f"Template file not found: {path}"
            raise FileNotFoundError(msg)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            msg = f"Template file must contain a JSON array, got {type(raw).__name__}"
            raise TypeError(msg)
        valid = [entry for entry in raw if isinstance(entry, dict)]
        self._templates: tuple[dict[str, JsonValue], ...] = tuple(valid)
        self._rng = (
            random.Random(seed)  # noqa: S311
            if seed is not None
            else random.Random()  # noqa: S311
        )

    async def generate(
        self,
        context: GenerationContext,
    ) -> tuple[TaskRequirement, ...]:
        """Sample requirements from the template bundle.

        Args:
            context: Generation context with ``domain``,
                ``complexity_range``, and ``count``.

        Returns:
            Tuple of ``TaskRequirement`` instances of length
            ``context.count``, or an empty tuple if no templates
            match the filters.
        """
        filtered = self._filter_templates(context)
        if not filtered:
            logger.warning(
                CLIENT_REQUIREMENT_GENERATED,
                strategy="template",
                matched=0,
                domain=context.domain,
                requested=context.count,
            )
            return ()

        samples = self._sample(filtered, context.count)
        requirements: list[TaskRequirement] = []
        for template in samples:
            try:
                requirements.append(self._to_requirement(template))
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                logger.warning(
                    CLIENT_REQUIREMENT_GENERATED,
                    strategy="template",
                    skipped=True,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        skipped = len(samples) - len(requirements)
        if skipped:
            logger.info(
                CLIENT_REQUIREMENT_GENERATED,
                strategy="template",
                generated=len(requirements),
                skipped=skipped,
                requested=context.count,
            )
        logger.debug(
            CLIENT_REQUIREMENT_GENERATED,
            strategy="template",
            generated=len(requirements),
            domain=context.domain,
        )
        return tuple(requirements)

    def _filter_templates(
        self,
        context: GenerationContext,
    ) -> tuple[dict[str, JsonValue], ...]:
        allowed = {c.value for c in context.complexity_range}

        def matches(template: dict[str, JsonValue]) -> bool:
            domain = template.get("domain")
            if domain is not None and domain != context.domain:
                return False
            complexity = template.get("complexity")
            return not (complexity is not None and complexity not in allowed)

        return tuple(t for t in self._templates if matches(t))

    def _sample(
        self,
        pool: tuple[dict[str, JsonValue], ...],
        count: int,
    ) -> list[dict[str, JsonValue]]:
        if count <= len(pool):
            return self._rng.sample(list(pool), count)
        extras = [self._rng.choice(pool) for _ in range(count - len(pool))]
        return list(pool) + extras

    @staticmethod
    def _to_requirement(template: dict[str, JsonValue]) -> TaskRequirement:
        title = template["title"]
        description = template["description"]
        if not isinstance(title, str) or not isinstance(description, str):
            msg = "title and description must be strings"
            raise TypeError(msg)
        criteria = template.get("acceptance_criteria", ())
        if not isinstance(criteria, list | tuple):
            criteria = ()
        return TaskRequirement(
            title=title,
            description=description,
            task_type=TaskType(str(template.get("task_type", "development"))),
            priority=Priority(str(template.get("priority", "medium"))),
            estimated_complexity=Complexity(
                str(template.get("estimated_complexity", "medium")),
            ),
            acceptance_criteria=tuple(str(c) for c in criteria),
        )
