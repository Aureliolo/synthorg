"""Post-execution memory hooks -- distillation, procedural memory, evolution.

Extracted from ``agent_engine.py`` to keep the engine file under 800 lines.
The functions here are standalone async helpers that receive all dependencies
as explicit parameters (no ``self``).
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    DISTILLATION_CAPTURE_FAILED,
    DISTILLATION_CAPTURE_SKIPPED,
)
from synthorg.observability.events.evolution import (
    EVOLUTION_TRIGGER_FAILED,
    EVOLUTION_TRIGGER_SKIPPED,
)
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_ERROR,
    PROCEDURAL_MEMORY_SKIPPED,
)

if TYPE_CHECKING:
    from synthorg.engine.evolution.service import EvolutionService
    from synthorg.engine.loop_protocol import ExecutionResult
    from synthorg.engine.recovery import RecoveryResult
    from synthorg.memory.procedural.models import ProceduralMemoryConfig
    from synthorg.memory.procedural.proposer import ProceduralMemoryProposer
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)


async def try_capture_distillation(
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    distillation_capture_enabled: bool,
    memory_backend: MemoryBackend | None,
) -> None:
    """Capture trajectory distillation at task completion (non-critical).

    Skips when disabled or no backend; failures are swallowed and
    logged.  System errors (``MemoryError``, ``RecursionError``)
    and cancellation propagate.

    Raises:
        asyncio.CancelledError: If the capture task is cancelled
            mid-flight.
    """
    if not distillation_capture_enabled:
        logger.debug(
            DISTILLATION_CAPTURE_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="capture_disabled",
        )
        return
    if memory_backend is None:
        logger.debug(
            DISTILLATION_CAPTURE_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="no_memory_backend",
        )
        return
    from pydantic import TypeAdapter  # noqa: PLC0415

    from synthorg.core.types import NotBlankStr  # noqa: PLC0415
    from synthorg.memory.consolidation import capture_distillation  # noqa: PLC0415

    try:
        _nb = TypeAdapter(NotBlankStr)
        await capture_distillation(
            execution_result,
            agent_id=_nb.validate_python(agent_id),
            task_id=_nb.validate_python(task_id),
            backend=memory_backend,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            DISTILLATION_CAPTURE_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            reason="validation_or_capture_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def try_procedural_memory(  # noqa: PLR0913
    execution_result: ExecutionResult,
    recovery_result: RecoveryResult | None,
    agent_id: str,
    task_id: str,
    *,
    procedural_proposer: ProceduralMemoryProposer | None,
    memory_backend: MemoryBackend | None,
    procedural_memory_config: ProceduralMemoryConfig | None = None,
) -> None:
    """Run procedural memory pipeline (non-critical, never fatal).

    Skips when proposer is absent or no recovery occurred.  System
    errors and cancellation propagate; all others are swallowed.

    Raises:
        asyncio.CancelledError: If the pipeline task is cancelled
            mid-flight.
    """
    if procedural_proposer is None or recovery_result is None:
        logger.debug(
            PROCEDURAL_MEMORY_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason=(
                "no_proposer" if procedural_proposer is None else "no_recovery_result"
            ),
        )
        return
    if memory_backend is None:  # pragma: no cover -- guarded by caller
        logger.debug(
            PROCEDURAL_MEMORY_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="no_memory_backend",
        )
        return
    try:
        from pydantic import TypeAdapter  # noqa: PLC0415

        from synthorg.core.types import NotBlankStr  # noqa: PLC0415
        from synthorg.memory.procedural.pipeline import (  # noqa: PLC0415
            propose_procedural_memory,
        )

        _nb = TypeAdapter(NotBlankStr)
        await propose_procedural_memory(
            execution_result,
            recovery_result,
            _nb.validate_python(agent_id),
            _nb.validate_python(task_id),
            proposer=procedural_proposer,
            memory_backend=memory_backend,
            config=procedural_memory_config,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROCEDURAL_MEMORY_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            phase="procedural_memory",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def try_evolution_trigger(
    agent_id: str,
    task_id: str,
    *,
    evolution_service: EvolutionService | None,
) -> None:
    """Run evolution pipeline post-task (non-critical, never fatal).

    Fires after distillation capture so distilled knowledge is
    available for evolution context.  System errors and cancellation
    propagate; all others are swallowed.

    Args:
        agent_id: Agent that executed the task.
        task_id: Task that was executed.
        evolution_service: Evolution service (None = skip).

    Raises:
        asyncio.CancelledError: If the trigger task is cancelled
            mid-flight.
    """
    if evolution_service is None:
        logger.debug(
            EVOLUTION_TRIGGER_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="no_evolution_service",
        )
        return
    try:
        from pydantic import TypeAdapter  # noqa: PLC0415

        from synthorg.core.types import NotBlankStr  # noqa: PLC0415

        _nb = TypeAdapter(NotBlankStr)
        await evolution_service.evolve(
            agent_id=_nb.validate_python(agent_id),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            EVOLUTION_TRIGGER_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            phase="evolution_trigger",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
