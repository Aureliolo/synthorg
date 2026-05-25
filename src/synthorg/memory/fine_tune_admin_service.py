# module-kind: complex_service
"""Fine-tune pipeline admin: run lifecycle, preflight, history.

Owns the fine-tune pipeline admin surface: run lifecycle (start /
resume / cancel / status), preflight validation, and the run-history
pagination path. Sibling to the checkpoint deploy / rollback / delete
surface on ``MemoryService``: the two surfaces use disjoint state
(this one needs ``run_repo`` + ``orchestrator``; the checkpoint surface
needs ``checkpoint_repo`` + ``_embedder_state_lock`` + settings) so
keeping them apart prevents the fine-tune orchestrator dependency
from leaking into the checkpoint flow and vice versa.

Constructor-injected with ``run_repo`` and ``orchestrator``;
:meth:`get_fine_tune_status` reads ``run_repo`` when a specific
``run_id`` is asked for. The fine-tune errors
(``FineTuneRunNotFoundError`` / ``FineTuneRunNotResumableError``) are
defined on ``synthorg.memory.service`` as part of its public error
surface; this module imports them lazily inside the methods that
raise them so the sibling-module pair is not a runtime import cycle.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRun,
    FineTuneStatus,
    PreflightCheck,
    PreflightResult,
)
from synthorg.memory.fine_tune_plan import (
    FineTunePlan,
    MemoryBackendUnsupportedError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
    MEMORY_FINE_TUNE_INVALID_REQUEST,
    MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED,
    MEMORY_FINE_TUNE_REQUESTED,
    MEMORY_FINE_TUNE_STARTED,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneRunRepository,  # noqa: TC001 -- runtime arg type
)

if TYPE_CHECKING:
    from synthorg.memory.embedding.fine_tune_orchestrator import FineTuneOrchestrator

logger = get_logger(__name__)


class FineTuneAdminService:
    """Fine-tune run lifecycle + preflight admin surface.

    Args:
        run_repo: Fine-tune run persistence. ``None`` disables every
            run-history method (``list_runs`` raises
            :class:`MemoryBackendUnsupportedError`).
        orchestrator: Fine-tune pipeline orchestrator. ``None`` on
            backends that do not support fine-tune runs; the lifecycle
            methods raise :class:`MemoryBackendUnsupportedError` in
            that case.
    """

    __slots__ = ("_orchestrator", "_runs")

    def __init__(
        self,
        *,
        run_repo: FineTuneRunRepository | None = None,
        orchestrator: FineTuneOrchestrator | None = None,
    ) -> None:
        self._runs = run_repo
        self._orchestrator = orchestrator

    def _require_runs(self) -> FineTuneRunRepository:
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

    def _require_orchestrator(self) -> FineTuneOrchestrator:
        if self._orchestrator is None:
            msg = (
                "fine-tune orchestration is not available on the active "
                "persistence backend (SQLite-only today)"
            )
            logger.warning(
                MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED,
                method="_require_orchestrator",
                reason="orchestrator_not_wired",
            )
            raise MemoryBackendUnsupportedError(msg)
        return self._orchestrator

    async def list_runs(
        self,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """Paginated newest-first runs + total count.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive.
            MemoryBackendUnsupportedError: When the active backend does
                not expose fine-tune runs.
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
        return await self._require_runs().list_items_page(limit=limit, offset=offset)

    async def start_fine_tune(self, plan: FineTunePlan) -> FineTuneRun:
        """Start a new fine-tune run from *plan*.

        Raises:
            MemoryBackendUnsupportedError: When the active backend does
                not expose fine-tune support.
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

        Translates the orchestrator's :class:`ValueError` (which packs
        both "run not found" and "stage not resumable" into the same
        exception type) into typed variants so MCP handlers can map them
        to ``not_found`` / ``conflict`` domain codes via
        ``exc.domain_code`` instead of regex-matching the message.

        Raises:
            MemoryBackendUnsupportedError: When the active backend does
                not expose fine-tune support.
            FineTuneRunNotFoundError: If *run_id* does not exist.
            FineTuneRunNotResumableError: If the run exists but is not
                in a resumable stage.
            RuntimeError: If another run is already active.
        """
        from synthorg.memory.service import (  # noqa: PLC0415
            FineTuneRunNotFoundError,
            FineTuneRunNotResumableError,
        )

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

        When ``run_id`` is omitted, returns the orchestrator's idea of
        the current / most-recent run. When provided, looks up the run
        directly from persistence so historical runs remain queryable
        after the in-memory ``current_run`` slot rotates.

        Raises:
            MemoryBackendUnsupportedError: When the backend does not
                support fine-tune runs.
            ValueError: If *run_id* is given but the run does not exist.
        """
        orchestrator = self._require_orchestrator()
        if run_id is None:
            return await orchestrator.get_status()
        run = await self._require_runs().get(str(run_id))
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
        awaits the background task for up to 30s.

        Returns:
            The run id of the cancelled run, or ``None`` if no run was
            active. Captured BEFORE ``cancel()`` runs because the
            orchestrator may clear ``current_run`` during cancellation.

        Raises:
            MemoryBackendUnsupportedError: When the backend does not
                support fine-tune runs.
        """
        orchestrator = self._require_orchestrator()
        active = orchestrator.current_run
        target_id = str(active.id) if active is not None else None
        # ``FineTuneOrchestrator.cancel`` already emits
        # ``MEMORY_FINE_TUNE_CANCELLED`` on a successful cancel and
        # nothing on the no-active-run branch. Returning the captured
        # id (without a second event) keeps the audit / metrics surface
        # single-source.
        await orchestrator.cancel()
        return target_id

    async def run_preflight(self, plan: FineTunePlan) -> PreflightResult:
        """Validate *plan* against local-env prerequisites.

        Minimal + deterministic so it is callable from any MCP client
        without kicking off the full pipeline: verifies that the
        ``source_dir`` exists and is a directory, that ``output_dir``
        (if provided) is writable, and that numeric overrides are
        within the runner's declared bounds.

        Raises:
            MemoryBackendUnsupportedError: When the backend does not
                support fine-tune runs.
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
    symlink does not silently pass the non-existent-parent fallback.
    """
    path = Path(output_dir)
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
    if not probe.is_dir():
        return PreflightCheck(
            name=NotBlankStr("output_dir_writable"),
            status="fail",
            message=NotBlankStr(
                f"Output directory path is not a directory: {probe}",
            ),
        )
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
    """Return a pass check; Pydantic already enforced the bounds.

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
        "No overrides; runner defaults will apply"
        if not non_default
        else f"Overrides within bounds: {non_default}"
    )
    return PreflightCheck(
        name=NotBlankStr("override_bounds"),
        status="pass",
        message=NotBlankStr(message),
    )
