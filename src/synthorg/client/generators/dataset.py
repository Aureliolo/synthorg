"""Dataset-backed requirement generator."""

import csv
import json
import random
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from synthorg.client.models import (
    GenerationContext,
    TaskRequirement,
)
from synthorg.core.enums import Complexity, Priority, TaskType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.client import CLIENT_REQUIREMENT_GENERATED

logger = get_logger(__name__)


class DatasetGenerator:
    """Samples requirements from a CSV or JSONL dataset file.

    Supported file extensions are ``.csv`` and ``.jsonl``. Required
    columns/fields per row are ``title`` and ``description``;
    optional fields are ``task_type``, ``priority``,
    ``estimated_complexity``, ``acceptance_criteria`` (JSON-encoded
    list in CSV rows, native list in JSONL), ``domain``, and
    ``complexity``. Malformed rows are logged and skipped.
    """

    def __init__(
        self,
        *,
        dataset_path: Path | str,
        seed: int | None = None,
    ) -> None:
        """Initialize the dataset generator.

        Args:
            dataset_path: Path to a ``.csv`` or ``.jsonl`` file.
            seed: Optional seed for reproducible sampling.

        Raises:
            FileNotFoundError: If ``dataset_path`` does not exist.
            ValueError: If the file extension is not supported.
        """
        path = Path(dataset_path)
        if not path.exists():
            msg = f"Dataset file not found: {path}"
            logger.warning(msg, path=str(path))
            raise FileNotFoundError(msg)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            self._rows = self._load_csv(path)
        elif suffix == ".jsonl":
            self._rows = self._load_jsonl(path)
        else:
            msg = f"Unsupported dataset extension: {suffix} (expected .csv or .jsonl)"
            raise ValueError(msg)
        self._rng = (
            random.Random(seed)  # noqa: S311
            if seed is not None
            else random.Random()  # noqa: S311
        )

    async def generate(
        self,
        context: GenerationContext,
    ) -> tuple[TaskRequirement, ...]:
        """Sample and convert dataset rows into requirements.

        Args:
            context: Generation context with ``domain``,
                ``complexity_range``, and ``count``.

        Returns:
            Tuple of validated requirements. Rows that fail
            validation are skipped with a warning log.
        """
        filtered = self._filter_rows(context)
        if not filtered:
            logger.warning(
                CLIENT_REQUIREMENT_GENERATED,
                strategy="dataset",
                matched=0,
                domain=context.domain,
                requested=context.count,
            )
            return ()
        samples = self._sample(filtered, context.count)
        requirements: list[TaskRequirement] = []
        for row in samples:
            try:
                requirements.append(self._to_requirement(row))
            except (KeyError, ValueError, ValidationError) as exc:
                logger.warning(
                    CLIENT_REQUIREMENT_GENERATED,
                    strategy="dataset",
                    skipped=True,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        logger.debug(
            CLIENT_REQUIREMENT_GENERATED,
            strategy="dataset",
            generated=len(requirements),
        )
        return tuple(requirements)

    def _filter_rows(
        self,
        context: GenerationContext,
    ) -> tuple[dict[str, Any], ...]:
        allowed = {c.value for c in context.complexity_range}

        def matches(row: dict[str, Any]) -> bool:
            domain = row.get("domain")
            if domain and domain != context.domain:
                return False
            complexity = row.get("complexity")
            return not (complexity and complexity not in allowed)

        return tuple(r for r in self._rows if matches(r))

    def _sample(
        self,
        pool: tuple[dict[str, Any], ...],
        count: int,
    ) -> list[dict[str, Any]]:
        if count <= len(pool):
            return self._rng.sample(list(pool), count)
        extras = [self._rng.choice(pool) for _ in range(count - len(pool))]
        return list(pool) + extras

    @staticmethod
    def _load_csv(path: Path) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                row: dict[str, Any] = dict(raw)
                criteria_raw = row.get("acceptance_criteria")
                if isinstance(criteria_raw, str) and criteria_raw:
                    try:
                        row["acceptance_criteria"] = json.loads(criteria_raw)
                    except json.JSONDecodeError:
                        row["acceptance_criteria"] = []
                rows.append(row)
        return tuple(rows)

    @staticmethod
    def _load_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        CLIENT_REQUIREMENT_GENERATED,
                        strategy="dataset",
                        skipped_line=lineno,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
                else:
                    logger.warning(
                        CLIENT_REQUIREMENT_GENERATED,
                        strategy="dataset",
                        skipped_line=lineno,
                        value_type=type(parsed).__name__,
                    )
        return tuple(rows)

    @staticmethod
    def _to_requirement(row: dict[str, Any]) -> TaskRequirement:
        criteria = row.get("acceptance_criteria", ())
        if not isinstance(criteria, list | tuple):
            criteria = ()
        return TaskRequirement(
            title=str(row["title"]),
            description=str(row["description"]),
            task_type=TaskType(row.get("task_type", "development")),
            priority=Priority(row.get("priority", "medium")),
            estimated_complexity=Complexity(
                row.get("estimated_complexity", "medium"),
            ),
            acceptance_criteria=tuple(str(c) for c in criteria),
        )
