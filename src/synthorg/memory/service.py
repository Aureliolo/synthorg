# module-kind: complex_service
"""Memory admin service layer for fine-tuning checkpoints and runs.

Encapsulates persistence access for the ``/memory/fine-tune/*``
endpoints so the controller stays thin (parse / shape / return) and
the raw ``app_state.persistence.get_db()`` handle stays inside the
persistence package where it belongs.

The service is backend-agnostic: both SQLite and Postgres expose the
``FineTuneRunRepository`` + ``FineTuneCheckpointRepository`` protocols
via ``PersistenceBackend.fine_tune_runs`` and
``PersistenceBackend.fine_tune_checkpoints``, and the parametrized
conformance suite at ``tests/conformance/persistence/`` exercises
both arms on every run. When an active backend still does not expose
those repos (or the orchestrator has not been wired for the current
deployment), the fine-tune lifecycle methods raise a typed
:class:`MemoryBackendUnsupportedError` so MCP handlers can route the
failure through the standard ``not_supported()`` envelope.

The fine-tune run lifecycle (start / resume / cancel / status /
preflight / list_runs) lives in
:class:`synthorg.memory.fine_tune_admin_service.FineTuneAdminService`;
``MemoryService``'s fine-tune methods are thin delegates so the
controller call surface is a single class. Checkpoint deploy /
rollback / delete share the ``_embedder_state_lock`` with the
``get_active_embedder`` snapshot read and the settings-rollback
state machine, so they stay together as one mutation boundary; the
fine-tune lifecycle has no overlap with that lock and lives in its
own sibling module to avoid coupling the orchestrator dependency
to the checkpoint flow.
"""

import asyncio
import json
from collections.abc import Awaitable
from typing import ClassVar, Literal

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
    FineTuneStatus,
    PreflightResult,
)
from synthorg.memory.fine_tune_plan import (
    ActiveEmbedderSnapshot,
    FineTunePlan,
    MemoryBackendUnsupportedError,
)
from synthorg.memory.ports import FineTuneOrchestratorPort, SettingsAccessor
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.memory import (
    MEMORY_CHECKPOINT_BACKUP_UNAVAILABLE,
    MEMORY_CHECKPOINT_DEPLOY_FAILED,
    MEMORY_CHECKPOINT_DEPLOYED,
    MEMORY_CHECKPOINT_NOT_FOUND,
    MEMORY_CHECKPOINT_REREAD_FAILED,
    MEMORY_CHECKPOINT_ROLLBACK,
    MEMORY_CHECKPOINT_ROLLBACK_FAILED,
    MEMORY_CHECKPOINT_ROLLBACK_STEP_FAILED,
    MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
    MEMORY_ENTRY_DELETE_FAILED,
    MEMORY_ENTRY_DELETED,
    MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,
    FineTuneRunRepository,
)

logger = get_logger(__name__)


# Three-valued ``_read_setting`` outcome. ``was_unset`` means the
# settings service confirmed the key was absent; ``read_failed`` means
# the service raised a non-NotFound exception, so rollback must leave
# the newly-written key untouched (it may be masking a real prior
# value we could not capture).
_PriorSettingState = Literal["was_set", "was_unset", "read_failed"]


class CheckpointNotFoundError(NotFoundError):
    """Raised when a deploy/rollback/delete targets a missing checkpoint.

    Inherits :class:`NotFoundError` so ``EXCEPTION_HANDLERS`` routes
    this through the 404 envelope rather than the generic INTERNAL
    fallback the bare ``DomainError`` base would imply.
    """

    __slots__ = ()
    is_retryable: bool = False  # deterministic: the checkpoint is absent
    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RECORD_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Checkpoint not found"


class CheckpointRollbackUnavailableError(ValidationError):
    """Raised when a rollback is requested but no backup config exists.

    Inherits :class:`ValidationError` so ``EXCEPTION_HANDLERS`` emits a
    422 envelope with the distinct ``CHECKPOINT_ROLLBACK_UNAVAILABLE``
    code: the checkpoint exists but its rollback prerequisite (a
    stored backup config) does not, so the dashboard can message the
    invalid rollback target precisely rather than show a blanket retry.
    """

    __slots__ = ()
    is_retryable: bool = False  # deterministic: no backup exists
    error_code: ClassVar[ErrorCode] = ErrorCode.CHECKPOINT_ROLLBACK_UNAVAILABLE
    default_message: ClassVar[str] = "No backup config available for this checkpoint"


class CheckpointRollbackCorruptError(ValidationError):
    """Raised when the stored backup config fails JSON parsing.

    Inherits :class:`ValidationError` (422) with the distinct
    ``CHECKPOINT_ROLLBACK_CORRUPT`` code so clients can tell a corrupt
    rollback backup from a generic validation failure.
    """

    __slots__ = ()
    is_retryable: bool = False  # deterministic: the stored payload is malformed
    error_code: ClassVar[ErrorCode] = ErrorCode.CHECKPOINT_ROLLBACK_CORRUPT
    default_message: ClassVar[str] = "Checkpoint rollback data is corrupt"


class FineTuneRunNotFoundError(NotFoundError):
    """Raised when a referenced fine-tune run id does not exist.

    Inherits :class:`NotFoundError` so ``EXCEPTION_HANDLERS`` routes
    this through the 404 envelope instead of the generic INTERNAL
    fallback the bare ``DomainError`` base would imply.
    """

    __slots__ = ()
    is_retryable: bool = False  # deterministic: the run is absent
    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RECORD_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Fine-tune run not found"
    # Wire-level ``domain_code`` so MCP handlers route via the shared
    # ``err(exc)`` helper instead of regex-matching exception messages.
    domain_code: str = "not_found"


class FineTuneRunNotResumableError(ConflictError):
    """Raised when a fine-tune run exists but is not in a resumable stage.

    Inherits :class:`ConflictError` so ``EXCEPTION_HANDLERS`` routes
    this through the 409 envelope, distinguishing a non-resumable
    stage from an internal failure.
    """

    __slots__ = ()
    is_retryable: bool = False  # deterministic: stage is terminal or running
    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Fine-tune run not resumable"
    domain_code: str = "conflict"


class MemoryService:
    """Service layer for memory admin operations.

    Wraps the fine-tune checkpoint + run repositories and the settings
    service so API controllers never reach into ``persistence.get_db()``
    directly.
    """

    __slots__ = (
        "_checkpoints",
        "_embedder_state_lock",
        "_ft_admin",
        "_memory_backend",
        "_orchestrator",
        "_runs",
        "_settings",
    )

    def __init__(
        self,
        *,
        checkpoint_repo: FineTuneCheckpointRepository | None = None,
        run_repo: FineTuneRunRepository | None = None,
        settings_service: SettingsAccessor | None = None,
        orchestrator: FineTuneOrchestratorPort | None = None,
        memory_backend: MemoryBackend | None = None,
    ) -> None:
        """Initialize with optional repository + settings + orchestrator deps.

        Every dependency is independently optional so a deployment
        that wires only a ``MemoryBackend`` (e.g. for the
        ``DELETE /memory/entries`` path) can construct the
        service without resolving fine-tune repositories. Each
        lifecycle method that needs a missing dep raises
        :class:`MemoryBackendUnsupportedError` at call time.

        Args:
            checkpoint_repo: Fine-tune checkpoint persistence. ``None``
                disables every checkpoint lifecycle method.
            run_repo: Fine-tune run persistence. ``None`` disables
                every run-history method.
            settings_service: Runtime settings service. ``None``
                degrades deploy flows to "activate only, skip
                settings push".
            orchestrator: Fine-tune pipeline orchestrator. ``None`` on
                backends that do not support fine-tune runs (the
                fine-tune lifecycle methods raise
                :class:`MemoryBackendUnsupportedError` in that case).
            memory_backend: Shared memory backend exposed to admin
                operations such as ``delete_memory_entry``. ``None``
                when no backend is wired (the entry-delete method
                raises :class:`MemoryBackendUnsupportedError` in that case).
        """
        from synthorg.memory.fine_tune_admin_service import (  # noqa: PLC0415
            FineTuneAdminService,
        )

        self._checkpoints = checkpoint_repo
        self._runs = run_repo
        self._settings = settings_service
        self._orchestrator = orchestrator
        self._memory_backend = memory_backend
        self._ft_admin = FineTuneAdminService(
            run_repo=run_repo,
            orchestrator=orchestrator,
        )
        # Serializes the three-step reads in ``get_active_embedder`` and
        # the multi-repo writes in ``deploy_checkpoint`` /
        # ``rollback_checkpoint`` / ``delete_checkpoint`` so a
        # concurrent deploy-then-read cannot observe ``checkpoint_id``
        # from one state and ``provider`` / ``model`` settings from
        # another. The lock is fine-grained to embedder-state paths
        # only; read-mostly endpoints (``list_checkpoints``,
        # ``list_runs``, ``get_checkpoint``) are not gated through it.
        self._embedder_state_lock = asyncio.Lock()

    def _require_checkpoints(self) -> FineTuneCheckpointRepository:
        """Return the checkpoint repo or raise ``MemoryBackendUnsupportedError``.

        Returns:
            Result of type ``FineTuneCheckpointRepository``.

        Raises:
            MemoryBackendUnsupportedError: If the operation is not supported by the
                active backend.
        """
        if self._checkpoints is None:
            msg = (
                "fine-tune checkpoint repository is not wired on the "
                "active persistence backend; checkpoint lifecycle "
                "operations are unavailable"
            )
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                repo="checkpoints",
                reason="repository_not_wired",
            )
            raise MemoryBackendUnsupportedError(msg)
        return self._checkpoints

    def _require_runs(self) -> FineTuneRunRepository:
        """Return the run repo or raise ``MemoryBackendUnsupportedError``.

        Returns:
            Result of type ``FineTuneRunRepository``.

        Raises:
            MemoryBackendUnsupportedError: If the operation is not supported by the
                active backend.
        """
        if self._runs is None:
            msg = (
                "fine-tune run repository is not wired on the active "
                "persistence backend; run-history operations are "
                "unavailable"
            )
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                repo="runs",
                reason="repository_not_wired",
            )
            raise MemoryBackendUnsupportedError(msg)
        return self._runs

    async def delete_memory_entry(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        """Delete a single memory entry owned by *agent_id*.

        Routes through the shared :class:`MemoryBackend` instance.
        Both arguments are validated as non-blank by the caller; the
        service trusts the contract and forwards to the backend.

        Args:
            agent_id: Owning agent identifier.
            memory_id: Backend-assigned memory identifier.

        Returns:
            ``True`` if the entry was deleted, ``False`` if not found.

        Raises:
            MemoryBackendUnsupportedError: When no memory backend is wired.
            Exception: Raised when the relevant invariant fails.
        """
        if self._memory_backend is None:
            msg = (
                "memory backend is not wired on the active application "
                "state; memory entry deletion is unavailable"
            )
            logger.warning(
                MEMORY_ENTRY_DELETE_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                reason="backend_unsupported",
            )
            raise MemoryBackendUnsupportedError(msg)
        try:
            deleted = await self._memory_backend.delete(agent_id, memory_id)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                MEMORY_ENTRY_DELETE_FAILED,
                exc,
                agent_id=agent_id,
                memory_id=memory_id,
                reason="backend_exception",
            )
            raise
        if deleted:
            logger.info(
                MEMORY_ENTRY_DELETED,
                agent_id=agent_id,
                memory_id=memory_id,
            )
        else:
            logger.warning(
                MEMORY_ENTRY_DELETE_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                reason="not_found",
            )
        return deleted

    async def list_checkpoints(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        """Return a page of checkpoints newest-first along with the total count.

        Both values are needed so callers (REST controllers, MCP
        handlers) can attach accurate pagination metadata without
        reaching past the service boundary.

        Args:
            limit: Page size.
            offset: Page offset.

        Returns:
            Tuple of ``(checkpoints, total)`` where ``total`` is the
            unfiltered count the repository would return for an
            unpaginated query.
        """
        return await self._require_checkpoints().list_items_page(
            limit=limit,
            offset=offset,
        )

    async def get_checkpoint(
        self,
        checkpoint_id: NotBlankStr,
    ) -> CheckpointRecord | None:
        """Fetch a single checkpoint by id.

        Returns:
            The matching ``CheckpointRecord``, or ``None`` when no match is found.
        """
        return await self._require_checkpoints().get(checkpoint_id)

    async def deploy_checkpoint(
        self,
        checkpoint_id: NotBlankStr,
    ) -> CheckpointRecord:
        """Activate *checkpoint_id* and update runtime embedder config.

        Captures the prior active checkpoint + settings, activates the
        target, and writes ``memory.embedder_model`` /
        ``memory.embedder_provider``. On any settings-side failure the
        prior state is restored atomically. Held under
        ``_embedder_state_lock`` so a concurrent
        :meth:`get_active_embedder` cannot observe a partially-updated
        checkpoint / settings pair.

        Returns:
            Result of type ``CheckpointRecord``.

        Raises:
            CheckpointNotFoundError: If the id does not exist.
            QueryError: On unrecoverable persistence faults.
        """
        checkpoints = self._require_checkpoints()
        async with self._embedder_state_lock:
            cp = await checkpoints.get(checkpoint_id)
            if cp is None:
                logger.warning(
                    MEMORY_CHECKPOINT_NOT_FOUND,
                    checkpoint_id=checkpoint_id,
                    operation="deploy",
                )
                msg = f"Checkpoint {checkpoint_id} not found"
                raise CheckpointNotFoundError(msg)

            prior = await checkpoints.get_active_checkpoint()
            await checkpoints.set_active(checkpoint_id)

            if self._settings is not None:
                await self._apply_deploy_settings(
                    checkpoint_id=checkpoint_id,
                    model_path=cp.model_path,
                    prior=prior,
                )

            updated = await checkpoints.get(checkpoint_id)
            if updated is None:
                # Disappearing between activation and re-read can only
                # be a concurrent delete; surface the contracted
                # CheckpointNotFoundError (404) so the caller sees a
                # deterministic "checkpoint no longer exists".
                logger.warning(
                    MEMORY_CHECKPOINT_REREAD_FAILED,
                    checkpoint_id=checkpoint_id,
                    operation="deploy",
                )
                msg = f"Checkpoint {checkpoint_id} was removed concurrently"
                raise CheckpointNotFoundError(msg)
        logger.info(
            MEMORY_CHECKPOINT_DEPLOYED,
            checkpoint_id=checkpoint_id,
            prior_checkpoint_id=prior.id if prior is not None else None,
        )
        return updated

    async def rollback_checkpoint(
        self,
        checkpoint_id: NotBlankStr,
    ) -> CheckpointRecord:
        """Restore the backup config stored with *checkpoint_id*.

        Held under ``_embedder_state_lock`` so a concurrent
        :meth:`get_active_embedder` cannot observe a mid-rollback
        settings state.

        Returns:
            Result of type ``CheckpointRecord``.

        Raises:
            CheckpointNotFoundError: If the id does not exist.
            CheckpointRollbackUnavailableError: If no backup was stored.
            CheckpointRollbackCorruptError: If the backup JSON cannot
                be parsed.
        """
        checkpoints = self._require_checkpoints()
        async with self._embedder_state_lock:
            cp = await checkpoints.get(checkpoint_id)
            if cp is None:
                logger.warning(
                    MEMORY_CHECKPOINT_NOT_FOUND,
                    checkpoint_id=checkpoint_id,
                    operation="rollback",
                )
                msg = f"Checkpoint {checkpoint_id} not found"
                raise CheckpointNotFoundError(msg)
            if cp.backup_config_json is None:
                logger.warning(
                    MEMORY_CHECKPOINT_BACKUP_UNAVAILABLE,
                    checkpoint_id=checkpoint_id,
                )
                msg = "No backup config available for this checkpoint"
                raise CheckpointRollbackUnavailableError(msg)

            if self._settings is not None:
                try:
                    parsed: object = json.loads(cp.backup_config_json)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning(
                        MEMORY_CHECKPOINT_ROLLBACK_FAILED,
                        checkpoint_id=checkpoint_id,
                        error_type=type(exc).__name__,
                    )
                    msg = "Backup config is corrupt and cannot be restored"
                    raise CheckpointRollbackCorruptError(msg) from exc
                if not isinstance(parsed, dict):
                    # ``json.loads`` happily returns ``list``, ``None``,
                    # ``str``, etc.; the rollback loop assumes a mapping
                    # and would crash with ``AttributeError`` on
                    # ``backup.items()``. Fail closed with the dedicated
                    # corruption error instead.
                    logger.warning(
                        MEMORY_CHECKPOINT_ROLLBACK_FAILED,
                        checkpoint_id=checkpoint_id,
                        error_type="BackupConfigNotMapping",
                        parsed_type=type(parsed).__name__,
                    )
                    msg = (
                        "Backup config must be a JSON object; got "
                        f"{type(parsed).__name__}"
                    )
                    raise CheckpointRollbackCorruptError(msg)
                backup: dict[str, object] = parsed
                for key, value in backup.items():
                    await self._settings.set("memory", key, str(value))

            await checkpoints.deactivate_all()
            updated = await checkpoints.get(checkpoint_id)
            if updated is None:
                # Disappearing right after deactivate_all can only be a
                # concurrent delete; surface the contracted
                # CheckpointNotFoundError (404) so the caller sees a
                # deterministic "checkpoint no longer exists".
                logger.warning(
                    MEMORY_CHECKPOINT_REREAD_FAILED,
                    checkpoint_id=checkpoint_id,
                    operation="rollback",
                )
                msg = f"Checkpoint {checkpoint_id} was removed concurrently"
                raise CheckpointNotFoundError(msg)
        logger.info(
            MEMORY_CHECKPOINT_ROLLBACK,
            checkpoint_id=checkpoint_id,
        )
        return updated

    async def delete_checkpoint(self, checkpoint_id: NotBlankStr) -> None:
        """Delete a checkpoint by id.

        The underlying repository is a silent no-op when the target
        does not exist, so we pre-check and surface
        :class:`CheckpointNotFoundError` here. The controller maps that
        to HTTP 404, keeping the contract identical across
        deploy / rollback / delete endpoints (all three surface 404 for
        missing checkpoints and 409 for a ``QueryError`` such as
        attempting to delete the currently-active checkpoint). Held
        under ``_embedder_state_lock`` so the repo-side "cannot delete
        the active checkpoint" rule is evaluated against the same
        active-checkpoint snapshot that a concurrent
        :meth:`get_active_embedder` would observe.

        Raises:
            CheckpointNotFoundError: If the id does not exist.
            QueryError: On unrecoverable persistence faults (including
                the domain rule "cannot delete the active checkpoint").
        """
        checkpoints = self._require_checkpoints()
        async with self._embedder_state_lock:
            existing = await checkpoints.get(checkpoint_id)
            if existing is None:
                logger.warning(
                    MEMORY_CHECKPOINT_NOT_FOUND,
                    checkpoint_id=checkpoint_id,
                    operation="delete",
                )
                msg = f"Checkpoint {checkpoint_id} not found"
                raise CheckpointNotFoundError(msg)
            deleted = await checkpoints.delete(checkpoint_id)
            if not deleted:
                # Lost a race with a concurrent cross-process delete
                # after the pre-check; surface the documented 404
                # rather than reporting a silent success.
                logger.warning(
                    MEMORY_CHECKPOINT_NOT_FOUND,
                    checkpoint_id=checkpoint_id,
                    operation="delete",
                )
                msg = f"Checkpoint {checkpoint_id} not found"
                raise CheckpointNotFoundError(msg)

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """Page of fine-tune runs newest-first + total (delegates to admin).

        Returns:
            Tuple ``(tuple[FineTuneRun, ...], int)``.
        """
        return await self._ft_admin.list_runs(limit=limit, offset=offset)

    # ── Fine-tune lifecycle (delegated) ────────────────────────────

    async def start_fine_tune(self, plan: FineTunePlan) -> FineTuneRun:
        """Start a new fine-tune run from *plan* (delegates to admin).

        Returns:
            Result of type ``FineTuneRun``.
        """
        return await self._ft_admin.start_fine_tune(plan)

    async def resume_fine_tune(self, run_id: NotBlankStr) -> FineTuneRun:
        """Resume a failed / cancelled run (delegates to admin).

        Returns:
            Result of type ``FineTuneRun``.
        """
        return await self._ft_admin.resume_fine_tune(run_id)

    async def get_fine_tune_status(
        self,
        run_id: NotBlankStr | None = None,
    ) -> FineTuneStatus:
        """Return the orchestrator status (delegates to admin).

        Returns:
            Result of type ``FineTuneStatus``.
        """
        return await self._ft_admin.get_fine_tune_status(run_id)

    async def cancel_fine_tune(self) -> str | None:
        """Cancel the active run (delegates to admin).

        Returns:
            The resulting ``str``, or ``None`` when unavailable.
        """
        return await self._ft_admin.cancel_fine_tune()

    async def run_preflight(self, plan: FineTunePlan) -> PreflightResult:
        """Validate *plan* against local-env prerequisites (delegates to admin).

        Returns:
            Result of type ``PreflightResult``.
        """
        return await self._ft_admin.run_preflight(plan)

    async def get_active_embedder(self) -> ActiveEmbedderSnapshot:
        """Return the active embedder snapshot read from settings.

        Combines the active checkpoint id (from
        :meth:`get_active_checkpoint`) with the
        ``memory.embedder_model`` / ``memory.embedder_provider``
        settings so MCP callers get a single atomic read. The
        ``_embedder_state_lock`` is held across all three reads so a
        concurrent deploy / rollback cannot interleave between them
        and leave the caller observing ``checkpoint_id`` from one
        state and ``provider`` / ``model`` from another.

        Returns:
            Result of type ``ActiveEmbedderSnapshot``.
        """
        checkpoints = self._require_checkpoints()
        async with self._embedder_state_lock:
            active_checkpoint = await checkpoints.get_active_checkpoint()
            if self._settings is None:
                return ActiveEmbedderSnapshot(
                    checkpoint_id=(
                        active_checkpoint.id if active_checkpoint is not None else None
                    ),
                    read_from_settings=False,
                )
            provider_value, _ = await self._read_setting("embedder_provider")
            model_value, _ = await self._read_setting("embedder_model")
        return ActiveEmbedderSnapshot(
            provider=(
                NotBlankStr(provider_value)
                if provider_value is not None and provider_value
                else None
            ),
            model=(
                NotBlankStr(model_value)
                if model_value is not None and model_value
                else None
            ),
            checkpoint_id=(
                active_checkpoint.id if active_checkpoint is not None else None
            ),
            read_from_settings=True,
        )

    def _require_orchestrator(self) -> FineTuneOrchestratorPort:
        """Return the orchestrator or raise :class:`MemoryBackendUnsupportedError`.

        Handlers catch the exception and surface a ``not_supported``
        envelope (see :mod:`synthorg.meta.mcp.handlers.memory`).

        Returns:
            Result of type ``FineTuneOrchestratorPort``.

        Raises:
            MemoryBackendUnsupportedError: If the operation is not supported by the
                active backend.
        """
        if self._orchestrator is None:
            msg = (
                "fine-tune orchestration is not available on the active "
                "persistence backend (SQLite-only today)"
            )
            # Log before raising so operators can see the failure
            # path in telemetry even when handlers swallow the
            # exception into a ``not_supported`` wire envelope.
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                method="_require_orchestrator",
                reason="orchestrator_not_wired",
            )
            raise MemoryBackendUnsupportedError(msg)
        return self._orchestrator

    async def _apply_deploy_settings(
        self,
        *,
        checkpoint_id: NotBlankStr,
        model_path: str,
        prior: CheckpointRecord | None,
    ) -> None:
        """Push embedder settings for a freshly-activated checkpoint.

        Rolls back the checkpoint activation + any already-applied
        settings if a subsequent ``set`` call fails, so a failed deploy
        leaves the prior config intact.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        assert self._settings is not None  # noqa: S101 - guarded by caller

        prior_model_value, prior_model_state = await self._read_setting(
            "embedder_model",
        )
        prior_provider_value, prior_provider_state = await self._read_setting(
            "embedder_provider",
        )

        checkpoints = self._require_checkpoints()
        try:
            await self._settings.set("memory", "embedder_model", model_path)
            await self._settings.set("memory", "embedder_provider", "local")
        except Exception as exc:
            reraise_critical(exc)
            if prior is not None:
                await self._rollback_step(
                    checkpoints.set_active(prior.id),
                    checkpoint_id=checkpoint_id,
                    step="reactivate_prior_checkpoint",
                )
            else:
                await self._rollback_step(
                    checkpoints.deactivate_all(),
                    checkpoint_id=checkpoint_id,
                    step="deactivate_all_checkpoints",
                )
            # Restore / delete / leave each setting based on the
            # three-valued prior state captured by ``_read_setting``.
            # ``read_failed`` explicitly leaves the newly-written key
            # in place so a transient read error cannot erase a real
            # pre-existing setting: "absent" and "read failed" must
            # stay distinct branches, never collapsed.
            await self._restore_or_delete(
                "embedder_model",
                prior_model_value,
                prior_model_state,
                checkpoint_id,
            )
            await self._restore_or_delete(
                "embedder_provider",
                prior_provider_value,
                prior_provider_state,
                checkpoint_id,
            )
            logger.warning(
                MEMORY_CHECKPOINT_DEPLOY_FAILED,
                checkpoint_id=checkpoint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _restore_or_delete(
        self,
        key: str,
        prior_value: str | None,
        prior_state: _PriorSettingState,
        checkpoint_id: str,
    ) -> None:
        """Restore *prior_value* or delete the key based on *prior_state*.

        Three branches, one per :class:`_PriorSettingState` value:

        * ``was_set`` -- restore the captured prior value.
        * ``was_unset`` -- delete the newly-written setting so rollback
          returns to a pristine "key absent" state.
        * ``read_failed`` -- leave the key untouched. A transient
          settings-service outage during the pre-deploy read could make
          a real existing value look absent; deleting it on rollback
          would erase a legitimate pre-deploy setting. Leaving it means
          the rollback is best-effort for this key, which the
          ``MEMORY_CHECKPOINT_ROLLBACK_STEP_FAILED`` telemetry already
          signals for operator review.
        """
        assert self._settings is not None  # noqa: S101 - guarded by caller
        if prior_state == "was_set" and prior_value is not None:
            await self._rollback_step(
                self._settings.set("memory", key, prior_value),
                checkpoint_id=checkpoint_id,
                step=f"restore_{key}",
            )
        elif prior_state == "was_unset":
            # Genuinely absent before the deploy: remove the newly
            # written value so rollback returns to a pristine state.
            await self._rollback_step(
                self._settings.delete("memory", key),
                checkpoint_id=checkpoint_id,
                step=f"delete_{key}",
            )
        # ``read_failed`` intentionally leaves the newly-written key in
        # place; the settings-read warning already fired from
        # :meth:`_read_setting` so operators can triage.

    async def _read_setting(
        self,
        key: str,
    ) -> tuple[str | None, _PriorSettingState]:
        """Best-effort read of a ``memory.<key>`` setting for rollback.

        Returns ``(value, state)`` where *state* distinguishes three
        cases that the rollback logic must handle differently:

        * ``"was_set"`` -- the setting existed with a concrete value.
          Rollback restores the captured value.
        * ``"was_unset"`` -- the setting was genuinely absent
          (``SettingNotFoundError``). Rollback deletes the newly
          written value.
        * ``"read_failed"`` -- the settings service raised any other
          exception (connection / auth / corruption). Rollback leaves
          the key untouched so a transient read error cannot erase a
          pre-existing setting on deploy failure.

        Returns:
            Tuple ``(str | None, _PriorSettingState)``.
        """
        if self._settings is None:
            return None, "was_unset"
        # SettingNotFoundError is the "setting genuinely absent" path
        # -- benign and stays at DEBUG. Anything else (connection /
        # auth / corruption) is operationally interesting and escalates
        # to WARNING so prod monitoring catches prolonged
        # settings-service outages during a checkpoint-deploy rollback.
        from synthorg.settings.errors import (  # noqa: PLC0415 -- cycle break
            SettingNotFoundError,
        )

        try:
            value = await self._settings.get("memory", key)
        except SettingNotFoundError as exc:
            logger.debug(
                MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                setting=key,
                error_type=type(exc).__name__,
                reason="read_for_rollback_not_found",
            )
            return None, "was_unset"
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                MEMORY_EMBEDDER_SETTINGS_READ_FAILED,
                setting=key,
                error_type=type(exc).__name__,
                reason="read_for_rollback_transient",
            )
            # Transient failure -- rollback must NOT delete this key or
            # it would erase a pre-existing setting we failed to
            # capture. ``_restore_or_delete`` observes ``read_failed``
            # and leaves the newly-written value in place.
            return None, "read_failed"
        return value.value, "was_set"

    @staticmethod
    async def _rollback_step(
        coro: Awaitable[object],
        *,
        checkpoint_id: str,
        step: str,
    ) -> None:
        """Run *coro* in a rollback path, logging any failure at WARNING.

        Rollback failures must never shadow the original deploy error
        (which is already being raised up the call stack), but they
        must be audit-visible so operators know the config may be in
        an inconsistent state. Uses the rollback-specific event so
        alerting can distinguish primary deploy failures from partial
        rollback conditions.
        """
        try:
            await coro
        except Exception as exc:
            reraise_critical(exc)
            # Emit both the aggregate event (broad dashboards /
            # alerting) AND the step-specific event so alerts can pick
            # up partial-rollback conditions distinctly from the
            # overall rollback failure signal.
            logger.warning(
                MEMORY_CHECKPOINT_ROLLBACK_FAILED,
                checkpoint_id=checkpoint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                stage="rollback",
                step=step,
            )
            log_exception_redacted(
                logger,
                MEMORY_CHECKPOINT_ROLLBACK_STEP_FAILED,
                exc,
                checkpoint_id=checkpoint_id,
                step=step,
            )
