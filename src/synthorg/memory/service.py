"""Memory admin service layer for fine-tuning checkpoints and runs.

Encapsulates persistence access for the ``/memory/fine-tune/*`` endpoints
so the controller stays thin (parse / shape / return) and the raw
``app_state.persistence.get_db()`` handle stays inside the persistence
package where it belongs.

The service is backend-agnostic: both SQLite and Postgres expose the
``FineTuneRunRepository`` + ``FineTuneCheckpointRepository`` protocols
via ``PersistenceBackend.fine_tune_runs`` and
``PersistenceBackend.fine_tune_checkpoints``, and the parametrized
conformance suite at ``tests/conformance/persistence/`` exercises both
arms on every run. When an active backend still does not expose those
repos (or the orchestrator has not been wired for the current
deployment), the fine-tune lifecycle methods raise a typed
:class:`BackendUnsupportedError` so MCP handlers can route the failure
through the standard ``not_supported()`` envelope.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from synthorg.core.domain_errors import DomainError
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
    FineTuneStatus,
    PreflightCheck,
    PreflightResult,
)
from synthorg.memory.fine_tune_plan import (
    ActiveEmbedderSnapshot,
    BackendUnsupportedError,
    FineTunePlan,
)
from synthorg.observability import get_logger
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
    MEMORY_FINE_TUNE_INVALID_REQUEST,
    MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED,
    MEMORY_FINE_TUNE_REQUESTED,
    MEMORY_FINE_TUNE_STARTED,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,  # noqa: TC001
    FineTuneRunRepository,  # noqa: TC001
)

if TYPE_CHECKING:
    from synthorg.memory.embedding.fine_tune_orchestrator import (
        FineTuneOrchestrator,
    )
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


# Three-valued ``_read_setting`` outcome. ``was_unset`` means the
# settings service confirmed the key was absent; ``read_failed`` means
# the service raised a non-NotFound exception, so rollback must leave
# the newly-written key untouched (it may be masking a real prior
# value we could not capture).
_PriorSettingState = Literal["was_set", "was_unset", "read_failed"]


class CheckpointNotFoundError(DomainError):
    """Raised when a deploy/rollback/delete targets a missing checkpoint.

    Inherits :class:`DomainError` (audit ``34-error-handling-consistency``)
    so the ``__init_subclass__`` validator sees the inherited
    ``ErrorCode.INTERNAL_ERROR`` (8000, prefix 8) matches the
    ``INTERNAL`` category.  Subclasses that need a more specific 404
    code should override the ``error_code`` / ``error_category`` /
    ``status_code`` ClassVars.
    """

    __slots__ = ()
    is_retryable: bool = False  # deterministic: the checkpoint is absent


class CheckpointRollbackUnavailableError(DomainError):
    """Raised when a rollback is requested but no backup config exists."""

    __slots__ = ()
    is_retryable: bool = False  # deterministic: no backup exists


class CheckpointRollbackCorruptError(DomainError):
    """Raised when the stored backup config fails JSON parsing."""

    __slots__ = ()
    is_retryable: bool = False  # deterministic: the stored payload is malformed


class FineTuneRunNotFoundError(DomainError):
    """Raised when a referenced fine-tune run id does not exist."""

    __slots__ = ()
    is_retryable: bool = False  # deterministic: the run is absent
    # Wire-level ``domain_code`` so MCP handlers can route via the
    # shared ``err(exc)`` helper instead of regex-matching the
    # exception message -- that was the pre-existing anti-pattern
    # this class replaces.
    domain_code: str = "not_found"


class FineTuneRunNotResumableError(DomainError):
    """Raised when a fine-tune run exists but is not in a resumable stage."""

    __slots__ = ()
    is_retryable: bool = False  # deterministic: stage is terminal or running
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
        settings_service: SettingsService | None = None,
        orchestrator: FineTuneOrchestrator | None = None,
        memory_backend: MemoryBackend | None = None,
    ) -> None:
        """Initialize with optional repository + settings + orchestrator deps.

        Every dependency is independently optional so a deployment
        that wires only a ``MemoryBackend`` (e.g. for the
        ``DELETE /memory/entries`` path) can construct the
        service without resolving fine-tune repositories. Each
        lifecycle method that needs a missing dep raises
        :class:`BackendUnsupportedError` at call time.

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
                :class:`BackendUnsupportedError` in that case).
            memory_backend: Shared memory backend exposed to admin
                operations such as ``delete_memory_entry``. ``None``
                when no backend is wired (the entry-delete method
                raises :class:`BackendUnsupportedError` in that case).
        """
        self._checkpoints = checkpoint_repo
        self._runs = run_repo
        self._settings = settings_service
        self._orchestrator = orchestrator
        self._memory_backend = memory_backend
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
        """Return the checkpoint repo or raise ``BackendUnsupportedError``."""
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
            raise BackendUnsupportedError(msg)
        return self._checkpoints

    def _require_runs(self) -> FineTuneRunRepository:
        """Return the run repo or raise ``BackendUnsupportedError``."""
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
            raise BackendUnsupportedError(msg)
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
            BackendUnsupportedError: When no memory backend is wired.
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
            raise BackendUnsupportedError(msg)
        try:
            deleted = await self._memory_backend.delete(agent_id, memory_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.exception(
                MEMORY_ENTRY_DELETE_FAILED,
                agent_id=agent_id,
                memory_id=memory_id,
                reason="backend_exception",
                error_type=type(exc).__name__,
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
        return await self._require_checkpoints().list_checkpoints(
            limit=limit,
            offset=offset,
        )

    async def get_checkpoint(
        self,
        checkpoint_id: NotBlankStr,
    ) -> CheckpointRecord | None:
        """Fetch a single checkpoint by id."""
        return await self._require_checkpoints().get_checkpoint(checkpoint_id)

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

        Raises:
            CheckpointNotFoundError: If the id does not exist.
            QueryError: On unrecoverable persistence faults.
        """
        checkpoints = self._require_checkpoints()
        async with self._embedder_state_lock:
            cp = await checkpoints.get_checkpoint(checkpoint_id)
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

            updated = await checkpoints.get_checkpoint(checkpoint_id)
            if updated is None:
                logger.error(
                    MEMORY_CHECKPOINT_REREAD_FAILED,
                    checkpoint_id=checkpoint_id,
                    operation="deploy",
                )
                msg = "Checkpoint activated but not found on re-read"
                raise QueryError(msg)
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

        Raises:
            CheckpointNotFoundError: If the id does not exist.
            CheckpointRollbackUnavailableError: If no backup was stored.
            CheckpointRollbackCorruptError: If the backup JSON cannot
                be parsed.
        """
        checkpoints = self._require_checkpoints()
        async with self._embedder_state_lock:
            cp = await checkpoints.get_checkpoint(checkpoint_id)
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
                    parsed: Any = json.loads(cp.backup_config_json)
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
                backup: dict[str, Any] = parsed
                for key, value in backup.items():
                    await self._settings.set("memory", key, str(value))

            await checkpoints.deactivate_all()
            updated = await checkpoints.get_checkpoint(checkpoint_id)
            if updated is None:
                logger.error(
                    MEMORY_CHECKPOINT_REREAD_FAILED,
                    checkpoint_id=checkpoint_id,
                    operation="rollback",
                )
                msg = "Checkpoint not found after rollback"
                raise QueryError(msg)
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
            existing = await checkpoints.get_checkpoint(checkpoint_id)
            if existing is None:
                logger.warning(
                    MEMORY_CHECKPOINT_NOT_FOUND,
                    checkpoint_id=checkpoint_id,
                    operation="delete",
                )
                msg = f"Checkpoint {checkpoint_id} not found"
                raise CheckpointNotFoundError(msg)
            await checkpoints.delete_checkpoint(checkpoint_id)

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """Return a page of fine-tune runs newest-first + the total count.

        The MCP surface needs both the page and the unfiltered total so
        ``PaginationMeta`` can be attached without a second round trip.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive. Enforcing the bounds here keeps
                invalid paging inputs from reaching the repository
                where the error mode is backend-specific.
        """
        if offset < 0:
            logger.warning(
                MEMORY_FINE_TUNE_INVALID_REQUEST,
                surface="list_runs",
                param="offset",
                value=offset,
            )
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            logger.warning(
                MEMORY_FINE_TUNE_INVALID_REQUEST,
                surface="list_runs",
                param="limit",
                value=limit,
            )
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        return await self._require_runs().list_runs(limit=limit, offset=offset)

    # ── Fine-tune lifecycle ────────────────────────────────────────

    async def start_fine_tune(self, plan: FineTunePlan) -> FineTuneRun:
        """Start a new fine-tune run from *plan*.

        Args:
            plan: MCP-facing fine-tune plan (shield over
                :class:`FineTuneRequest`).

        Returns:
            The created run record.

        Raises:
            BackendUnsupportedError: When the active backend does not
                expose fine-tune support.
            RuntimeError: If another run is already active.
        """
        orchestrator = self._require_orchestrator()
        logger.info(
            MEMORY_FINE_TUNE_REQUESTED,
            source_dir=plan.source_dir,
            base_model=plan.base_model,
            resume_run_id=plan.resume_run_id,
        )
        run = await orchestrator.start(plan.to_request())
        logger.info(
            MEMORY_FINE_TUNE_STARTED,
            run_id=run.id,
            source_dir=plan.source_dir,
        )
        return run

    async def resume_fine_tune(self, run_id: NotBlankStr) -> FineTuneRun:
        """Resume a failed / cancelled fine-tune run.

        Translates the orchestrator's ``ValueError`` (which packs both
        "run not found" and "stage not resumable" into the same
        exception type) into typed variants so MCP handlers can map
        them to ``not_found`` / ``conflict`` domain codes via
        ``exc.domain_code`` instead of regex-matching the message.

        Raises:
            BackendUnsupportedError: When the active backend does not
                expose fine-tune support.
            FineTuneRunNotFoundError: If *run_id* does not exist.
            FineTuneRunNotResumableError: If the run exists but is not
                in a resumable stage (running, already completed, etc.).
            RuntimeError: If another run is already active.
        """
        orchestrator = self._require_orchestrator()
        try:
            return await orchestrator.resume(str(run_id))
        except ValueError as exc:
            message = str(exc).lower()
            if "not resumable" in message or "cannot resume" in message:
                raise FineTuneRunNotResumableError(str(exc)) from exc
            raise FineTuneRunNotFoundError(str(exc)) from exc

    async def get_fine_tune_status(
        self,
        run_id: NotBlankStr | None = None,
    ) -> FineTuneStatus:
        """Return the current orchestrator status.

        When ``run_id`` is omitted, returns the orchestrator's
        idea of the current / most-recent run (matches
        :meth:`FineTuneOrchestrator.get_status`). When provided, looks
        up the run directly from persistence and synthesises a status
        envelope (so historical runs remain queryable after the
        in-memory ``current_run`` slot rotates).

        Raises:
            BackendUnsupportedError: When the backend does not support
                fine-tune runs.
            ValueError: If *run_id* is given but the run does not exist.
        """
        orchestrator = self._require_orchestrator()
        if run_id is None:
            return await orchestrator.get_status()
        run = await self._require_runs().get_run(str(run_id))
        if run is None:
            logger.warning(
                MEMORY_FINE_TUNE_INVALID_REQUEST,
                surface="get_fine_tune_status",
                param="run_id",
                value=str(run_id),
                reason="run_not_found",
            )
            msg = f"Fine-tune run {run_id!r} not found"
            raise ValueError(msg)
        return FineTuneStatus(
            run_id=run.id,
            stage=run.stage,
            progress=run.progress,
            error=run.error,
        )

    async def cancel_fine_tune(self) -> str | None:
        """Cancel the currently active fine-tune run.

        The orchestrator tracks exactly one active run, so this is
        scoped to that run. Completing a cancel is cooperative and
        awaits the background task for up to 30s (see
        :meth:`FineTuneOrchestrator.cancel`).

        Returns:
            The run id of the cancelled run, or ``None`` if no run was
            active at the time ``cancel`` was issued. Captured **before**
            ``cancel()`` runs because the orchestrator may clear
            ``current_run`` during cancellation, and the MCP handler
            needs the id for the ``MCP_DESTRUCTIVE_OP_EXECUTED`` audit
            record.

        Raises:
            BackendUnsupportedError: When the backend does not support
                fine-tune runs.
        """
        orchestrator = self._require_orchestrator()
        active = orchestrator.current_run
        target_id = str(active.id) if active is not None else None
        # ``FineTuneOrchestrator.cancel`` already emits
        # ``MEMORY_FINE_TUNE_CANCELLED`` on a successful cancel (and
        # nothing on the no-active-run branch). Emitting a second
        # event here would (a) double-count real cancellations in
        # dashboards keyed on the event name and (b) produce a false
        # "cancelled" event when ``target_id is None``. Return the
        # captured id for the MCP audit path without re-emitting.
        await orchestrator.cancel()
        return target_id

    async def run_preflight(self, plan: FineTunePlan) -> PreflightResult:
        """Validate *plan* against local-env prerequisites.

        Keeps the check minimal + deterministic so it is callable from
        any MCP client without kicking off the full pipeline: verifies
        that the ``source_dir`` exists and is a directory, that
        ``output_dir`` (if provided) is writable (or at least
        creatable), and that numeric overrides are within the runner's
        declared bounds.

        Raises:
            BackendUnsupportedError: When the backend does not support
                fine-tune runs.
        """
        self._require_orchestrator()
        checks: list[PreflightCheck] = []
        checks.append(_check_source_dir_exists(plan.source_dir))
        if plan.output_dir is not None:
            checks.append(_check_output_dir_writable(plan.output_dir))
        checks.append(_check_overrides(plan))
        result = PreflightResult(checks=tuple(checks))
        logger.info(
            MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED,
            can_proceed=result.can_proceed,
            check_count=len(checks),
        )
        return result

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

    def _require_orchestrator(self) -> FineTuneOrchestrator:
        """Return the orchestrator or raise :class:`BackendUnsupportedError`.

        Handlers catch the exception and surface a ``not_supported``
        envelope (see :mod:`synthorg.meta.mcp.handlers.memory`).
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
            raise BackendUnsupportedError(msg)
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
            # pre-existing setting -- safer than the old ``bool``
            # design that collapsed "absent" and "read failed" into
            # the same branch.
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
        coro: Any,
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
            # Emit both the legacy aggregate event (for existing
            # dashboards / alerting) AND the step-specific event so
            # alerts can pick up partial-rollback conditions distinctly
            # from the overall rollback failure signal.
            logger.warning(
                MEMORY_CHECKPOINT_ROLLBACK_FAILED,
                checkpoint_id=checkpoint_id,
                error_type=type(exc).__name__,
                stage="rollback",
                step=step,
            )
            logger.error(  # noqa: TRY400 -- not an exception-context log; distinct audit event, not a traceback dump
                MEMORY_CHECKPOINT_ROLLBACK_STEP_FAILED,
                checkpoint_id=checkpoint_id,
                error_type=type(exc).__name__,
                step=step,
            )


# ── Preflight helpers ────────────────────────────────────────────


def _check_source_dir_exists(source_dir: str) -> PreflightCheck:
    """Verify that *source_dir* exists, is a directory, and is readable."""
    path = Path(source_dir)
    if not path.exists():
        return PreflightCheck(
            name=NotBlankStr("source_dir_exists"),
            status="fail",
            message=NotBlankStr(f"Source directory does not exist: {source_dir}"),
        )
    if not path.is_dir():
        return PreflightCheck(
            name=NotBlankStr("source_dir_exists"),
            status="fail",
            message=NotBlankStr(f"Source path is not a directory: {source_dir}"),
        )
    # Verify the runner can actually read AND traverse the directory.
    # R_OK alone is insufficient: a directory without execute/search
    # permission cannot be entered, so the runner would fail to open
    # any file inside even though ``R_OK`` passed.
    if not os.access(path, os.R_OK | os.X_OK):
        return PreflightCheck(
            name=NotBlankStr("source_dir_exists"),
            status="fail",
            message=NotBlankStr(
                f"Source directory is not readable or not traversable: {source_dir}",
            ),
        )
    return PreflightCheck(
        name=NotBlankStr("source_dir_exists"),
        status="pass",
        message=NotBlankStr("Source directory exists and is readable"),
    )


def _check_output_dir_writable(output_dir: str) -> PreflightCheck:
    """Verify that *output_dir* (or its parent, if absent) is writable.

    Resolves symlinks before the writability probe so a dangling
    symlink (``output_dir`` is a symlink whose target does not exist)
    does not silently pass the non-existent-parent fallback. The
    resolved target must exist and be writable for the check to pass.
    """
    path = Path(output_dir)
    # Symlink -> must resolve its target before probing. A dangling
    # symlink's ``exists()`` returns False (exists() follows the link),
    # so detect this case explicitly so the warn/fail message can
    # point to the broken target rather than reporting the link path.
    if path.is_symlink():
        resolved = path.resolve(strict=False)
        if not resolved.exists():
            return PreflightCheck(
                name=NotBlankStr("output_dir_writable"),
                status="warn",
                message=NotBlankStr(
                    f"Output directory symlink target does not exist: "
                    f"{path} -> {resolved}",
                ),
                detail="The runner will attempt to create it at pipeline start.",
            )
        probe = resolved
    else:
        probe = path if path.exists() else path.parent
    if not probe.exists():
        return PreflightCheck(
            name=NotBlankStr("output_dir_writable"),
            status="warn",
            message=NotBlankStr(
                f"Output directory parent does not exist: {probe}",
            ),
            detail="The runner will attempt to create it at pipeline start.",
        )
    # Probe must be a directory -- a path that exists but resolves
    # to a regular file cannot serve as the checkpoint output dir
    # and would fail deep inside the runner where the error is
    # harder to act on.
    if not probe.is_dir():
        return PreflightCheck(
            name=NotBlankStr("output_dir_writable"),
            status="fail",
            message=NotBlankStr(
                f"Output directory path is not a directory: {probe}",
            ),
        )
    # Require both write and execute/search permissions: writing a
    # new checkpoint file requires traversing into the directory
    # first, so W_OK alone is insufficient.
    if not os.access(probe, os.W_OK | os.X_OK):
        return PreflightCheck(
            name=NotBlankStr("output_dir_writable"),
            status="fail",
            message=NotBlankStr(
                f"Output directory is not writable or not traversable: {probe}",
            ),
        )
    return PreflightCheck(
        name=NotBlankStr("output_dir_writable"),
        status="pass",
        message=NotBlankStr("Output directory is writable"),
    )


def _check_overrides(plan: FineTunePlan) -> PreflightCheck:
    """Return a pass check -- Pydantic already enforced the bounds.

    Preserved as an explicit check so the preflight report always
    includes the override review; any operator reading the result gets
    an audit-trail style confirmation rather than a silent omission.
    """
    overrides = {
        "epochs": plan.epochs,
        "learning_rate": plan.learning_rate,
        "temperature": plan.temperature,
        "top_k": plan.top_k,
        "batch_size": plan.batch_size,
        "validation_split": plan.validation_split,
    }
    non_default = {k: v for k, v in overrides.items() if v is not None}
    message = (
        "No overrides -- runner defaults will apply"
        if not non_default
        else f"Overrides within bounds: {non_default}"
    )
    return PreflightCheck(
        name=NotBlankStr("override_bounds"),
        status="pass",
        message=NotBlankStr(message),
    )
