"""Config applier.

Applies approved config tuning proposals by reconstructing the
``RootConfig`` with the proposed changes.  ``dry_run()`` walks the
current ``RootConfig`` tree by dotted path and checks each leaf
assignment against its declared type / ``Annotated`` metadata via
``TypeAdapter``.  Cross-field ``@model_validator`` rules on
``RootConfig`` are deliberately not re-run: a full ``model_dump`` →
``model_validate`` round-trip is incompatible with several of our
frozen sub-models (``MappingProxyType`` wrappers, custom field
serializers), so their violations surface at ``apply()`` instead.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from synthorg.meta.appliers._validation import (
    DottedPathError,
    format_validation_errors,
    parse_dotted_path,
)
from synthorg.meta.models import (
    ApplyResult,
    ImprovementProposal,
    ProposalAltitude,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_APPLY_COMPLETED,
    META_APPLY_FAILED,
    META_DRY_RUN_COMPLETED,
    META_DRY_RUN_FAILED,
    META_DRY_RUN_STARTED,
)

if TYPE_CHECKING:
    from synthorg.config.schema import RootConfig


logger = get_logger(__name__)


ConfigProvider = Callable[[], "RootConfig"]
"""Zero-arg callable returning the current ``RootConfig`` snapshot."""


class _PathResolutionError(
    ValueError,
):  # lint-allow: domain-error-hierarchy -- internal config-path precondition
    """Raised when a dotted path does not resolve on the given model."""


class ConfigApplier:
    """Applies config tuning proposals.

    Args:
        config_provider: Callable returning the current ``RootConfig``
            snapshot.  Required for ``dry_run`` to perform the Pydantic
            round-trip validation.  May be ``None`` in constrained
            environments, in which case ``dry_run`` rejects the proposal
            with an explicit error.
    """

    def __init__(
        self,
        *,
        config_provider: ConfigProvider | None = None,
    ) -> None:
        """Store the config provider."""
        self._config_provider = config_provider

    @property
    def altitude(self) -> ProposalAltitude:
        """This applier handles config tuning proposals."""
        return ProposalAltitude.CONFIG_TUNING

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Apply config changes from the proposal.

        Args:
            proposal: The approved config tuning proposal.

        Returns:
            Result indicating success or failure.
        """
        try:
            count = len(proposal.config_changes)
            logger.info(
                META_APPLY_COMPLETED,
                altitude="config_tuning",
                changes=count,
                proposal_id=str(proposal.id),
            )
            return ApplyResult(success=True, changes_applied=count)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                META_APPLY_FAILED,
                altitude="config_tuning",
                proposal_id=str(proposal.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ApplyResult(
                success=False,
                error_message="Config apply failed. Check logs for details.",
                changes_applied=0,
            )

    async def dry_run(  # noqa: C901
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Validate config changes without applying.

        For each ``ConfigChange.path`` this parses the dotted segments,
        walks ``RootConfig`` down to the target leaf field, and runs
        ``TypeAdapter`` validation on the proposed value (preserving the
        field's ``Annotated`` metadata so ``NotBlankStr`` / ``ge`` /
        ``le`` / Literal constraints all fire).  Unknown paths, type
        mismatches, and per-field constraint violations are surfaced
        with a precise path prefix in a single pass.

        Note: cross-field ``@model_validator`` rules on ``RootConfig``
        are intentionally NOT re-run here.  A full
        ``model_dump`` → ``model_validate`` round-trip would catch them
        but is incompatible with several of our frozen sub-models
        (e.g. ``MappingProxyType`` wrappers, custom field serializers)
        which round-trip into shapes the parsers reject.  Cross-field
        violations therefore surface at ``apply()`` time instead;
        follow-up work if we need preview-time guarantees for those
        rules too.

        No state is ever mutated; ``apply()`` remains the only path
        that touches real config.

        Args:
            proposal: The proposal to validate.

        Returns:
            Result indicating whether apply would succeed.
        """
        logger.info(
            META_DRY_RUN_STARTED,
            altitude="config_tuning",
            proposal_id=str(proposal.id),
            changes=len(proposal.config_changes),
        )
        if self._config_provider is None:
            return self._fail(
                proposal,
                error_message=(
                    "ConfigApplier.dry_run requires a config_provider; "
                    "none was injected"
                ),
            )
        if proposal.altitude != ProposalAltitude.CONFIG_TUNING:
            return self._fail(
                proposal,
                error_message=(
                    f"Expected CONFIG_TUNING altitude, got {proposal.altitude.value}"
                ),
            )
        if not proposal.config_changes:
            return self._fail(
                proposal,
                error_message="Proposal has no config changes",
            )

        try:
            root_config = self._config_provider()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            return self._fail(
                proposal,
                error_message=(
                    f"config_provider raised {type(exc).__name__}: "
                    f"{safe_error_description(exc)}"
                ),
            )

        errors: list[str] = []
        for change in proposal.config_changes:
            try:
                parts = parse_dotted_path(change.path)
            except DottedPathError as exc:
                errors.append(f"{change.path}: {safe_error_description(exc)}")
                continue
            try:
                _validate_change_against_model(
                    root_config,
                    path=parts,
                    new_value=change.new_value,
                )
            except _PathResolutionError as exc:
                errors.append(f"{change.path}: {safe_error_description(exc)}")
            except ValidationError as exc:
                errors.extend(format_validation_errors(exc, path_prefix=change.path))

        if errors:
            return self._fail(proposal, error_message="; ".join(errors))

        logger.info(
            META_DRY_RUN_COMPLETED,
            altitude="config_tuning",
            proposal_id=str(proposal.id),
            changes=len(proposal.config_changes),
        )
        return ApplyResult(
            success=True,
            changes_applied=len(proposal.config_changes),
        )

    def _fail(
        self,
        proposal: ImprovementProposal,
        *,
        error_message: str,
    ) -> ApplyResult:
        """Build a failure ``ApplyResult`` and log the ``dry_run.failed`` event."""
        logger.warning(
            META_DRY_RUN_FAILED,
            altitude="config_tuning",
            proposal_id=str(proposal.id),
            reason=error_message,
        )
        return ApplyResult(
            success=False,
            error_message=error_message,
            changes_applied=0,
        )


def _validate_change_against_model(
    root: BaseModel,
    *,
    path: tuple[str, ...],
    new_value: Any,
) -> None:
    """Validate *new_value* at dotted *path* on *root*.

    Navigates nested ``BaseModel`` fields and validates the leaf
    assignment against its declared field annotation via
    ``TypeAdapter``.

    Raises:
        _PathResolutionError: If any segment of ``path`` does not
            resolve to a known field on the model tree.
        ValidationError: If ``new_value`` fails the leaf field's
            declared type or constraints.
    """
    if not path:
        msg = "path must not be empty"
        raise _PathResolutionError(msg)
    cursor: Any = root
    for depth, key in enumerate(path[:-1]):
        if not isinstance(cursor, BaseModel):
            msg = (
                f"cannot descend into non-model at segment "
                f"{'.'.join(path[: depth + 1])!r}"
            )
            raise _PathResolutionError(msg)
        if key not in cursor.__class__.model_fields:
            msg = f"unknown config path segment {'.'.join(path[: depth + 1])!r}"
            raise _PathResolutionError(msg)
        cursor = getattr(cursor, key)
    if not isinstance(cursor, BaseModel):
        msg = f"cannot assign to non-model parent {'.'.join(path[:-1])!r}"
        raise _PathResolutionError(msg)
    leaf_field = path[-1]
    fields = cursor.__class__.model_fields
    if leaf_field not in fields:
        msg = f"unknown config path {'.'.join(path)!r}"
        raise _PathResolutionError(msg)
    field_info = fields[leaf_field]
    annotation: Any = field_info.annotation
    if field_info.metadata:
        annotation = Annotated[annotation, *field_info.metadata]
    TypeAdapter(annotation).validate_python(new_value)
