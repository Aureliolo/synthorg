"""Shutdown strategy factory.

Builds a ``ShutdownStrategy`` from configuration, selecting between the
cooperative-timeout strategy (in ``synthorg.engine.shutdown``) and the
immediate / finish-tool / checkpoint strategies defined in their own
sibling modules.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock
from synthorg.engine.shutdown import (
    CheckpointSaver,
    CooperativeTimeoutStrategy,
    ShutdownStrategy,
)
from synthorg.engine.shutdown_checkpoint import CheckpointAndStopStrategy
from synthorg.engine.shutdown_finish_tool import FinishCurrentToolStrategy
from synthorg.engine.shutdown_immediate import ImmediateCancelStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_SHUTDOWN_TASK_ERROR,
)

if TYPE_CHECKING:
    from synthorg.config.schema import GracefulShutdownConfig

logger = get_logger(__name__)


def build_shutdown_strategy(
    config: GracefulShutdownConfig,
    *,
    checkpoint_saver: CheckpointSaver | None = None,
    clock: Clock | None = None,
) -> ShutdownStrategy:
    """Build a shutdown strategy from configuration.

    Args:
        config: Shutdown configuration with strategy name and params.
        checkpoint_saver: Optional checkpoint callback for the
            ``"checkpoint"`` strategy.
        clock: Injectable time source threaded into the strategy so
            factory-built call sites keep deterministic-time injection;
            defaults to ``SystemClock`` inside each strategy.

    Returns:
        Configured shutdown strategy instance.

    Raises:
        ValueError: If ``config.strategy`` is not a known strategy
            name.
    """
    strategies: dict[str, Callable[[], ShutdownStrategy]] = {
        "cooperative_timeout": lambda: CooperativeTimeoutStrategy(
            grace_seconds=config.grace_seconds,
            cleanup_seconds=config.cleanup_seconds,
            clock=clock,
        ),
        "immediate": lambda: ImmediateCancelStrategy(
            cleanup_seconds=config.cleanup_seconds,
            clock=clock,
        ),
        "finish_tool": lambda: FinishCurrentToolStrategy(
            tool_timeout_seconds=config.tool_timeout_seconds,
            cleanup_seconds=config.cleanup_seconds,
            clock=clock,
        ),
        "checkpoint": lambda: CheckpointAndStopStrategy(
            grace_seconds=config.grace_seconds,
            cleanup_seconds=config.cleanup_seconds,
            checkpoint_saver=checkpoint_saver,
            clock=clock,
        ),
    }

    builder = strategies.get(config.strategy)
    if builder is None:
        msg = (
            f"Unknown shutdown strategy: {config.strategy!r}. "
            f"Known strategies: {sorted(strategies)}"
        )
        logger.warning(
            EXECUTION_SHUTDOWN_TASK_ERROR,
            error=msg,
            strategy=config.strategy,
            known_strategies=sorted(strategies),
        )
        raise ValueError(msg)

    return builder()
