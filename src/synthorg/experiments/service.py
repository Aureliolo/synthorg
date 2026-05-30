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
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import (
    _MAX_VARIANT_WEIGHT,
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

_HASH_DIGEST_BYTES: Final[int] = 4
"""Number of leading SHA-256 digest bytes folded into the bucket. Four
bytes produce a ``uint32`` so the cumulative-weight walk stays
deterministic across Python versions (the canonical hashlib algorithm
is stable). The natural ``uint32`` range already bounds the bucket
before the final ``% modulus`` reduction, so no intermediate ceiling
modulo is needed."""


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

        Returns:
            The persisted ``ExperimentVariant`` record, including its
            ``created_at`` timestamp.

        Raises:
            ValidationError: When ``weight`` is non-positive (mirrors
                the Pydantic model bound for callers that build a
                ``weight`` dynamically rather than from a frozen DTO).
        """
        if weight < 1 or weight > _MAX_VARIANT_WEIGHT:
            logger.warning(
                EXPERIMENT_VARIANT_INVALID_WEIGHT,
                experiment=str(experiment),
                variant=str(variant),
                weight=weight,
            )
            msg = f"weight must be between 1 and {_MAX_VARIANT_WEIGHT}"
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
        """Remove a variant.

        Returns:
            ``True`` when a matching row was deleted, ``False`` when no
            such variant existed.
        """
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

        Returns:
            The subject's ``ExperimentAssignment``: the previously recorded
            one on replay, or the canonical assignment after computing and
            persisting a fresh one (re-read so a concurrent writer that
            landed first stays authoritative).

        Raises:
            NotFoundError: When the experiment has no registered
                variants.
            ConflictError: When a concurrent writer won the insert race and
                the canonical assignment cannot be re-read afterwards.
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
        try:
            await self._repo.record_assignment(assignment)
        except ConflictError:
            # A concurrent writer won the insert race against a durable
            # repository whose ``record_assignment`` enforces a unique
            # constraint on ``(experiment, subject_id)``. Re-read the
            # canonical assignment instead of failing: the choice is
            # deterministic so the winning row carries the same variant,
            # only the ``assigned_at`` timestamp differs.
            canonical = await self._repo.get_assignment(
                experiment=experiment,
                subject_id=subject_id,
            )
            if canonical is None:
                raise
            return canonical
        # Re-fetch after a successful record so a concurrent writer that
        # landed first (race between get_assignment and
        # record_assignment under a last-write-wins backend like the
        # in-memory repo) is the authoritative record. The choice is
        # deterministic so both writers chose the same variant; only
        # ``assigned_at`` differs, and the first writer's timestamp is
        # the canonical one.
        canonical = await self._repo.get_assignment(
            experiment=experiment,
            subject_id=subject_id,
        )
        result = canonical if canonical is not None else assignment
        logger.info(
            EXPERIMENT_ASSIGNMENT_COMPUTED,
            experiment=str(experiment),
            subject_id=str(subject_id),
            variant=str(result.variant),
            variant_count=len(variants),
        )
        return result

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
        """Walk the cumulative weight bracket and pick the matching variant.

        Returns:
            The variant whose cumulative-weight bracket contains the
            subject's stable bucket; the last variant when rounding leaves
            the bucket at the top of the range.
        """
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
        raw = int.from_bytes(
            digest[:_HASH_DIGEST_BYTES],
            byteorder="big",
            signed=False,
        )
        return raw % modulus
