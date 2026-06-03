"""In-memory :class:`ExperimentRepository` implementation.

Used for tests and for deployments that do not require durability on
the experiment registry. The repository is async-safe via an internal
:class:`asyncio.Lock` so concurrent writes do not race; it does not
persist anything across process restarts.
"""

# ruff: noqa: D102 -- protocol-method overrides; docstrings live on the protocol.

import asyncio
from dataclasses import dataclass
from datetime import datetime

from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import (
    ExperimentAssignment,
    ExperimentVariant,
)


@dataclass(frozen=True, slots=True)
class _VariantKey:
    experiment: str
    variant: str


@dataclass(frozen=True, slots=True)
class _AssignmentKey:
    experiment: str
    subject_id: str


class InMemoryExperimentRepository:
    """Backing-store-free implementation of :class:`ExperimentRepository`."""

    def __init__(self) -> None:
        self._variants: dict[_VariantKey, ExperimentVariant] = {}
        self._variant_order: list[_VariantKey] = []
        self._assignments: dict[_AssignmentKey, ExperimentAssignment] = {}
        self._lock = asyncio.Lock()

    async def save(self, variant: ExperimentVariant) -> None:
        key = _VariantKey(
            experiment=str(variant.experiment),
            variant=str(variant.variant),
        )
        async with self._lock:
            if key not in self._variants:
                self._variant_order.append(key)
            self._variants[key] = variant

    async def list_for_experiment(
        self,
        experiment: NotBlankStr,
    ) -> tuple[ExperimentVariant, ...]:
        async with self._lock:
            matches = [
                self._variants[k]
                for k in self._variant_order
                if k.experiment == str(experiment)
            ]
        return tuple(matches)

    async def delete(
        self,
        *,
        experiment: NotBlankStr,
        variant: NotBlankStr,
    ) -> bool:
        key = _VariantKey(experiment=str(experiment), variant=str(variant))
        async with self._lock:
            if key not in self._variants:
                return False
            del self._variants[key]
            self._variant_order = [k for k in self._variant_order if k != key]
            return True

    async def record_assignment(
        self,
        assignment: ExperimentAssignment,
    ) -> None:
        key = _AssignmentKey(
            experiment=str(assignment.experiment),
            subject_id=str(assignment.subject_id),
        )
        async with self._lock:
            self._assignments[key] = assignment

    async def get_assignment(
        self,
        *,
        experiment: NotBlankStr,
        subject_id: NotBlankStr,
    ) -> ExperimentAssignment | None:
        key = _AssignmentKey(
            experiment=str(experiment),
            subject_id=str(subject_id),
        )
        async with self._lock:
            return self._assignments.get(key)

    async def list_assignments(
        self,
        experiment: NotBlankStr,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ExperimentAssignment, ...], int]:
        async with self._lock:
            matches = sorted(
                (
                    a
                    for k, a in self._assignments.items()
                    if k.experiment == str(experiment)
                ),
                key=lambda a: a.assigned_at,
                reverse=True,
            )
        total = len(matches)
        offset = max(0, offset)
        end = offset + max(0, limit)
        return tuple(matches[offset:end]), total

    async def assigned_at(self, *, now: datetime) -> datetime:
        return now

    async def clear(self) -> None:
        """Drop every variant and assignment.

        Used by tests between scenarios; not part of the
        ``ExperimentRepository`` protocol because production
        repositories should not expose a clear-all surface.
        """
        async with self._lock:
            self._variants.clear()
            self._variant_order.clear()
            self._assignments.clear()
