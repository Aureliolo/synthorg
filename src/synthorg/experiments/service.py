"""A/B experiment registry service.

Wraps :class:`ExperimentRepository` with the variant-registration and
deterministic-assignment logic. Operators interact with the service
exclusively through the controller (REST) or MCP surface; the service
itself never accepts raw dicts.

Assignment is deterministic: same ``(experiment, subject_id)``
ALWAYS lands on the same variant for a given variant set. Adding a
new variant to an experiment may shift previously-recorded
assignments because the cumulative-weight bracket walk changes;
the service records every assignment on first computation so the
historical assignment is preserved across variant edits (lookup
returns the recorded assignment when one exists).
"""

import hashlib
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import (
    ExperimentAssignment,
    ExperimentVariant,
)
from synthorg.observability import get_logger
from synthorg.observability.events.experiments import (
    EXPERIMENT_ASSIGNMENT_COMPUTED,
    EXPERIMENT_ASSIGNMENT_REPLAYED,
    EXPERIMENT_NOT_FOUND,
    EXPERIMENT_VARIANT_DELETED,
    EXPERIMENT_VARIANT_INVALID_WEIGHT,
    EXPERIMENT_VARIANT_REGISTERED,
)

if TYPE_CHECKING:
    from synthorg.experiments.protocol import ExperimentRepository

logger = get_logger(__name__)

_HASH_BUCKET_MAX = 2**32
"""Upper bound on the hash bucket. The hash output is folded into a
``uint32`` so the cumulative-weight walk stays deterministic across
Python versions (the canonical hashlib algorithm is stable)."""


class ExperimentService:
    """Variant registration + deterministic assignment surface."""

    __slots__ = ("_clock", "_repo")

    def __init__(
        self,
        *,
        repository: ExperimentRepository,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repository
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def register_variant(
        self,
        *,
        experiment: NotBlankStr,
        variant: NotBlankStr,
        weight: int,
        description: str = "",
    ) -> ExperimentVariant:
        """Insert or replace a variant.

        Raises:
            ValidationError: When ``weight`` is non-positive (mirrors
                the Pydantic model bound for callers that build a
                ``weight`` dynamically rather than from a frozen DTO).
        """
        if weight < 1:
            logger.warning(
                EXPERIMENT_VARIANT_INVALID_WEIGHT,
                experiment=str(experiment),
                variant=str(variant),
                weight=weight,
            )
            msg = "weight must be >= 1"
            raise ValidationError(msg)
        record = ExperimentVariant(
            experiment=experiment,
            variant=variant,
            weight=weight,
            description=description,
            created_at=self._clock.now(),
        )
        await self._repo.save(record)
        logger.info(
            EXPERIMENT_VARIANT_REGISTERED,
            experiment=str(experiment),
            variant=str(variant),
            weight=weight,
        )
        return record

    async def list_variants(
        self,
        experiment: NotBlankStr,
    ) -> tuple[ExperimentVariant, ...]:
        """Return every variant registered for the experiment."""
        return await self._repo.list_for_experiment(experiment)

    async def delete_variant(
        self,
        *,
        experiment: NotBlankStr,
        variant: NotBlankStr,
    ) -> bool:
        """Remove a variant; returns ``True`` if a row was deleted."""
        removed = await self._repo.delete(
            experiment=experiment,
            variant=variant,
        )
        if removed:
            logger.info(
                EXPERIMENT_VARIANT_DELETED,
                experiment=str(experiment),
                variant=str(variant),
            )
        return removed

    async def assign(
        self,
        *,
        experiment: NotBlankStr,
        subject_id: NotBlankStr,
    ) -> ExperimentAssignment:
        """Assign ``subject_id`` to a variant deterministically.

        On the first call for a subject, computes the assignment by
        hashing ``(experiment, subject_id)`` and walking the variant
        list's cumulative-weight bracket. Subsequent calls return the
        recorded assignment unchanged even if the variant set has
        since shifted; the historical assignment is the authoritative
        record.

        Raises:
            NotFoundError: When the experiment has no registered
                variants.
        """
        recorded = await self._repo.get_assignment(
            experiment=experiment,
            subject_id=subject_id,
        )
        if recorded is not None:
            logger.info(
                EXPERIMENT_ASSIGNMENT_REPLAYED,
                experiment=str(experiment),
                subject_id=str(subject_id),
                variant=str(recorded.variant),
            )
            return recorded
        variants = await self._repo.list_for_experiment(experiment)
        if not variants:
            logger.warning(
                EXPERIMENT_NOT_FOUND,
                experiment=str(experiment),
                subject_id=str(subject_id),
                reason="no_variants_registered",
            )
            msg = f"Experiment {experiment!r} has no registered variants"
            raise NotFoundError(msg)
        chosen = self._choose_variant(experiment, subject_id, variants)
        assignment = ExperimentAssignment(
            experiment=experiment,
            subject_id=subject_id,
            variant=NotBlankStr(chosen.variant),
            assigned_at=self._clock.now(),
        )
        await self._repo.record_assignment(assignment)
        logger.info(
            EXPERIMENT_ASSIGNMENT_COMPUTED,
            experiment=str(experiment),
            subject_id=str(subject_id),
            variant=chosen.variant,
            variant_count=len(variants),
        )
        return assignment

    async def list_assignments(
        self,
        experiment: NotBlankStr,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ExperimentAssignment, ...], int]:
        """Return ``(page, total)`` for the experiment's assignments."""
        return await self._repo.list_assignments(
            experiment,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _choose_variant(
        experiment: NotBlankStr,
        subject_id: NotBlankStr,
        variants: tuple[ExperimentVariant, ...],
    ) -> ExperimentVariant:
        """Walk the cumulative weight bracket and pick the matching variant."""
        total = sum(v.weight for v in variants)
        bucket = ExperimentService._stable_bucket(experiment, subject_id, modulus=total)
        cumulative = 0
        for v in variants:
            cumulative += v.weight
            if bucket < cumulative:
                return v
        return variants[-1]

    @staticmethod
    def _stable_bucket(
        experiment: NotBlankStr,
        subject_id: NotBlankStr,
        *,
        modulus: int,
    ) -> int:
        """Return a deterministic bucket in ``[0, modulus)``.

        SHA-256 is used (rather than Python's ``hash``) so two
        processes with different ``PYTHONHASHSEED`` produce identical
        assignments. The leading 4 bytes are folded into a ``uint32``
        and then reduced modulo ``modulus``; the bias from non-power
        moduli is negligible at the variant-weight scale this service
        targets.
        """
        material = f"{experiment}\x1f{subject_id}".encode()
        digest = hashlib.sha256(material).digest()
        raw = int.from_bytes(digest[:4], byteorder="big", signed=False)
        return (raw % _HASH_BUCKET_MAX) % modulus
