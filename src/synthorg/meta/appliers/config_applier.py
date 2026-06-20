"""Config applier.

Validates and applies approved config tuning proposals against the
``RootConfig`` schema. ``apply()`` persists each :class:`ConfigChange`
through the injected :class:`SettingsWritePort` (the DB precedence
tier) with best-effort reverse-order rollback on partial failure.
``dry_run()`` walks the
current ``RootConfig`` tree by dotted path and checks each leaf
assignment against its declared type / ``Annotated`` metadata via
``TypeAdapter``.  Cross-field ``@model_validator`` rules on
``RootConfig`` are deliberately not re-run: a full ``model_dump`` →
``model_validate`` round-trip is incompatible with several of our
frozen sub-models (``MappingProxyType`` wrappers, custom field
serializers), so their violations surface at ``apply()`` instead.
"""

import json
from collections.abc import Callable
from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, TypeAdapter, ValidationError

from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
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
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.meta import (
    META_APPLY_COMPLETED,
    META_APPLY_FAILED,
    META_DRY_RUN_COMPLETED,
    META_DRY_RUN_FAILED,
    META_DRY_RUN_STARTED,
)

logger = get_logger(__name__)


ConfigProvider = Callable[[], "RootConfig"]
"""Zero-arg callable returning the current ``RootConfig`` snapshot."""


@runtime_checkable
class SettingsWritePort(Protocol):
    """Minimal read/write seam the config applier needs to persist changes.

    Structurally satisfied by
    :class:`~synthorg.settings.service.SettingsService`; narrowing to
    this port keeps the applier free of the full service surface and
    lets tests substitute an in-memory double.
    """

    async def get(self, namespace: str, key: str) -> object:
        """Return the resolved setting (raises if the key is unknown)."""
        ...

    async def set(
        self,
        namespace: str,
        key: str,
        value: str,
    ) -> object:
        """Persist *value* for ``namespace/key`` (raises on validation)."""
        ...


def _serialise(value: object) -> str:
    """Serialise a JSON config value into the settings string form.

    Returns:
        The string a :class:`SettingsService` ``set`` accepts.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value)


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
        settings_writer: Read/write seam used by ``apply`` to persist
            each change through the settings service (DB precedence
            tier).  ``None`` makes ``apply`` reject the proposal rather
            than silently no-op.
    """

    def __init__(
        self,
        *,
        config_provider: ConfigProvider | None = None,
        settings_writer: SettingsWritePort | None = None,
    ) -> None:
        """Store the config provider and the settings write seam."""
        self._config_provider = config_provider
        self._settings_writer = settings_writer

    @property
    def altitude(self) -> ProposalAltitude:
        """This applier handles config tuning proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.CONFIG_TUNING

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Persist config changes from the proposal via the settings service.

        Each :class:`ConfigChange` path is split into a
        ``namespace/key`` pair and written through the injected
        :class:`SettingsWritePort` (the DB precedence tier). The prior
        value of every key is captured first so a mid-batch failure
        rolls every already-applied change back, leaving the settings
        store as it was before the call (best-effort: a rollback write
        that itself fails is logged).

        Args:
            proposal: The approved config tuning proposal.

        Returns:
            Result indicating success or failure.
        """
        if self._settings_writer is None:
            logger.warning(
                META_APPLY_FAILED,
                altitude="config_tuning",
                proposal_id=str(proposal.id),
                reason="no settings_writer injected",
            )
            return ApplyResult(
                success=False,
                error_message="Config apply requires a settings writer; none wired.",
                changes_applied=0,
            )
        if not proposal.config_changes:
            return ApplyResult(success=True, changes_applied=0)

        targets: list[tuple[str, str, object]] = []
        for change in proposal.config_changes:
            namespace, _, key = change.path.partition(".")
            if not namespace or not key:
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="config_tuning",
                    proposal_id=str(proposal.id),
                    reason=f"config path {change.path!r} is not 'namespace.key'",
                )
                return ApplyResult(
                    success=False,
                    error_message=(
                        f"config path {change.path!r} is not 'namespace.key'"
                    ),
                    changes_applied=0,
                )
            targets.append((namespace, key, change.new_value))

        writer = self._settings_writer
        applied: list[tuple[str, str, str]] = []
        try:
            for namespace, key, new_value in targets:
                previous = await writer.get(namespace, key)
                old_value = getattr(previous, "value", "")
                await writer.set(namespace, key, _serialise(new_value))
                applied.append((namespace, key, str(old_value)))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            rollback_failures = await self._rollback(applied, proposal=proposal)
            log_exception_redacted(
                logger,
                META_APPLY_FAILED,
                exc,
                altitude="config_tuning",
                proposal_id=str(proposal.id),
            )
            return ApplyResult(
                success=False,
                error_message=(
                    "Config apply failed; rollback was incomplete and the "
                    "settings store may be partially applied."
                    if rollback_failures > 0
                    else "Config apply failed and was rolled back."
                ),
                changes_applied=0,
            )

        logger.info(
            META_APPLY_COMPLETED,
            altitude="config_tuning",
            changes=len(applied),
            proposal_id=str(proposal.id),
        )
        return ApplyResult(success=True, changes_applied=len(applied))

    async def _rollback(
        self,
        applied: list[tuple[str, str, str]],
        *,
        proposal: ImprovementProposal,
    ) -> int:
        """Restore previously-captured values after a failed apply.

        A rollback write that itself fails is logged and skipped so one
        bad key cannot abort the rest of the restoration.

        Returns:
            The number of rollback writes that failed; ``0`` means the
            store was fully restored.
        """
        if self._settings_writer is None:
            return 0
        writer = self._settings_writer
        failures = 0
        for namespace, key, old_value in reversed(applied):
            try:
                await writer.set(namespace, key, old_value)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                failures += 1
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="config_tuning",
                    proposal_id=str(proposal.id),
                    reason="rollback_write_failed",
                    namespace=namespace,
                    key=key,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        return failures

    async def dry_run(
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        """Build a failure ``ApplyResult`` and log the ``dry_run.failed`` event.

        Returns:
            ``ApplyResult`` instance.
        """
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
    new_value: object,
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
    cursor: object = root
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
    annotation: object = field_info.annotation
    if field_info.metadata:
        annotation = Annotated[annotation, *field_info.metadata]
    TypeAdapter(annotation).validate_python(new_value)
