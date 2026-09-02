"""Recovery strategy factory for ``AgentEngine``.

Dispatches :class:`EngineRecoveryConfig.strategy` to the matching
:class:`RecoveryStrategy` implementation. ``CHECKPOINT`` requires a
connected :class:`CheckpointRepository`; absence raises
:class:`RecoveryConfigError` so the misconfiguration surfaces at boot
rather than at recovery time.
"""

from typing import assert_never

from synthorg.engine.checkpoint.wiring import CheckpointWiring
from synthorg.engine.errors import RecoveryConfigError
from synthorg.engine.recovery import FailAndReassignStrategy, RecoveryStrategy
from synthorg.engine.recovery_config import (
    EngineRecoveryConfig,
    RecoveryStrategyType,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_RECOVERY_FAILED

logger = get_logger(__name__)


def build_recovery_strategy(
    config: EngineRecoveryConfig,
    *,
    checkpointing: CheckpointWiring | None,
) -> RecoveryStrategy:
    """Construct the configured :class:`RecoveryStrategy`.

    Args:
        config: Engine recovery configuration discriminator.
        checkpointing: The checkpoint repositories and their
            configuration, or ``None`` when persistence is unconnected.
            Required when ``config.strategy`` is
            :attr:`RecoveryStrategyType.CHECKPOINT`; ignored by the
            fail-reassign path.

    Returns:
        The recovery strategy matching the discriminator.

    Raises:
        RecoveryConfigError: ``config.strategy`` is ``CHECKPOINT`` and
            nothing was wired to checkpoint into.
    """
    match config.strategy:
        case RecoveryStrategyType.FAIL_REASSIGN:
            return FailAndReassignStrategy()
        case RecoveryStrategyType.CHECKPOINT:
            if checkpointing is None:
                msg = (
                    "RecoveryStrategyType.CHECKPOINT requires checkpointing "
                    "to be wired through build_recovery_strategy (the boot "
                    "assembly supplies it from the active PersistenceBackend "
                    "and config.recovery.checkpoint)."
                )
                logger.error(
                    EXECUTION_RECOVERY_FAILED,
                    phase="config_validation",
                    recovery_strategy=RecoveryStrategyType.CHECKPOINT.value,
                    error_type="RecoveryConfigError",
                    error=msg,
                )
                raise RecoveryConfigError(msg)
            from synthorg.engine.checkpoint.strategy import (  # noqa: PLC0415
                CheckpointRecoveryStrategy,
            )

            return CheckpointRecoveryStrategy(wiring=checkpointing)
        case _:  # pragma: no cover
            assert_never(config.strategy)
