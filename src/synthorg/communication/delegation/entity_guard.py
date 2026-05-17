"""Entity alignment guard for delegation validation.

Validates that entity definitions are aligned between delegator and
delegatee at delegation time.  Four configurable modes control the
enforcement level, from no-op to strict rejection.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- runtime for Pydantic
from types import MappingProxyType
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.ontology import (
    ONTOLOGY_GUARD_BLOCKED,
    ONTOLOGY_GUARD_DRIFT_DETECTED,
    ONTOLOGY_GUARD_STAMPED,
)
from synthorg.ontology.config import GuardMode

if TYPE_CHECKING:
    from synthorg.communication.delegation.models import DelegationRequest
    from synthorg.ontology.config import DelegationGuardConfig
    from synthorg.persistence.ontology_protocol import OntologyEntityRepository

logger = get_logger(__name__)


class EntityGuardOutcome(BaseModel):
    """Result of the entity alignment guard check.

    Attributes:
        passed: Whether the delegation is allowed.
        mechanism: Guard mechanism name (always ``entity_alignment``).
        message: Human-readable detail (empty on success).
        entity_versions: Version manifest captured during check.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    passed: bool = Field(description="Whether the delegation is allowed")
    mechanism: NotBlankStr = Field(
        default="entity_alignment",
        description="Guard mechanism name",
    )
    message: str = Field(
        default="",
        description="Human-readable detail",
    )
    entity_versions: Mapping[str, int] | None = Field(
        default=None,
        description="Entity version manifest at check time",
    )

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Enforce pass/fail message consistency."""
        if self.passed and self.message:
            msg = "message must be empty when passed is True"
            raise ValueError(msg)
        if not self.passed and not self.message:
            msg = "message is required when passed is False"
            raise ValueError(msg)
        return self


class EntityAlignmentGuard:
    """Validate entity alignment during delegation.

    Checks that delegator and delegatee share the same entity version
    understanding.  Four configurable modes:

    - ``none``: Guard disabled, no overhead.
    - ``stamp``: Records entity version manifest (audit only).
    - ``validate``: Stamps + logs WARNING on version mismatch.
    - ``enforce``: Stamps + rejects delegation on stale versions.

    Args:
        ontology: Ontology backend for version manifest retrieval.
        config: Delegation guard configuration.
    """

    __slots__ = ("_config", "_ontology")

    def __init__(
        self,
        *,
        ontology: OntologyEntityRepository,
        config: DelegationGuardConfig,
    ) -> None:
        self._ontology = ontology
        self._config = config

    async def check(  # noqa: C901, PLR0911, PLR0912
        self,
        request: DelegationRequest,
    ) -> EntityGuardOutcome:
        """Run entity alignment validation.

        Args:
            request: The delegation request to validate.

        Returns:
            Guard outcome with pass/fail status and version manifest.
        """
        mode = self._config.guard_mode

        if mode == GuardMode.NONE:
            return EntityGuardOutcome(passed=True)

        try:
            manifest = await self._ontology.get_version_manifest()
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                ONTOLOGY_GUARD_BLOCKED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                detail="Failed to retrieve entity version manifest",
            )
            if mode == GuardMode.ENFORCE:
                return EntityGuardOutcome(
                    passed=False,
                    message="Entity alignment check failed: "
                    "could not retrieve version manifest",
                )
            manifest = {}

        frozen_manifest: Mapping[str, int] = (
            MappingProxyType(copy.deepcopy(manifest))
            if manifest
            else MappingProxyType({})
        )

        if mode == GuardMode.STAMP:
            logger.debug(
                ONTOLOGY_GUARD_STAMPED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                entity_count=len(frozen_manifest),
            )
            return EntityGuardOutcome(
                passed=True,
                entity_versions=frozen_manifest,
            )

        # Check version alignment for VALIDATE and ENFORCE modes.
        # Compare the request's known versions against the current
        # manifest to detect stale entity knowledge.
        mismatches: list[str] = []
        if request.entity_versions:
            for name, known_ver in request.entity_versions.items():
                current_ver = manifest.get(name)
                if current_ver is None:
                    mismatches.append(f"{name}: known v{known_ver}, not in manifest")
                elif current_ver != known_ver:
                    mismatches.append(
                        f"{name}: known v{known_ver}, current v{current_ver}"
                    )

        if mode == GuardMode.VALIDATE:
            logger.debug(
                ONTOLOGY_GUARD_STAMPED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                entity_count=len(frozen_manifest),
            )
            if not manifest:
                logger.warning(
                    ONTOLOGY_GUARD_DRIFT_DETECTED,
                    delegator=request.delegator_id,
                    delegatee=request.delegatee_id,
                    detail="No entities registered in ontology",
                )
            elif mismatches:
                logger.warning(
                    ONTOLOGY_GUARD_DRIFT_DETECTED,
                    delegator=request.delegator_id,
                    delegatee=request.delegatee_id,
                    detail=f"Version mismatches: {', '.join(mismatches)}",
                    mismatch_count=len(mismatches),
                )
            return EntityGuardOutcome(
                passed=True,
                entity_versions=frozen_manifest,
            )

        # GuardMode.ENFORCE
        if not manifest:
            logger.warning(
                ONTOLOGY_GUARD_BLOCKED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                detail="No entities registered -- cannot enforce alignment",
            )
            return EntityGuardOutcome(
                passed=False,
                entity_versions=frozen_manifest,
                message="Entity alignment enforcement failed: "
                "no entities registered in ontology",
            )

        if mismatches:
            detail = f"Stale entity versions: {', '.join(mismatches)}"
            logger.warning(
                ONTOLOGY_GUARD_BLOCKED,
                delegator=request.delegator_id,
                delegatee=request.delegatee_id,
                detail=detail,
                mismatch_count=len(mismatches),
            )
            return EntityGuardOutcome(
                passed=False,
                entity_versions=frozen_manifest,
                message=f"Entity alignment enforcement failed: {detail}",
            )

        logger.debug(
            ONTOLOGY_GUARD_STAMPED,
            delegator=request.delegator_id,
            delegatee=request.delegatee_id,
            entity_count=len(frozen_manifest),
        )
        return EntityGuardOutcome(
            passed=True,
            entity_versions=frozen_manifest,
        )

    @property
    def guard_mode(self) -> GuardMode:
        """Current guard enforcement mode."""
        return self._config.guard_mode
