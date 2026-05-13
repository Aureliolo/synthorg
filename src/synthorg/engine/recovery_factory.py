"""Recovery strategy factory for ``AgentEngine``.

Dispatches :class:`EngineRecoveryConfig.strategy` to the matching
:class:`RecoveryStrategy` implementation. ``CHECKPOINT`` requires a
connected :class:`CheckpointRepository`; absence raises
:class:`RecoveryConfigError` so the misconfiguration surfaces at boot
rather than at recovery time.
"""

from typing import TYPE_CHECKING

from synthorg.engine.errors import RecoveryConfigError
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.engine.recovery_config import (
    EngineRecoveryConfig,
    RecoveryStrategyType,
)

if TYPE_CHECKING:
    from synthorg.engine.checkpoint.models import CheckpointConfig
    from synthorg.engine.recovery import RecoveryStrategy
    from synthorg.persistence.checkpoint_protocol import (
        CheckpointRepository,
        HeartbeatRepository,
    )


def build_recovery_strategy(
    config: EngineRecoveryConfig,
    *,
    checkpoint_repo: CheckpointRepository | None = None,
    heartbeat_repo: HeartbeatRepository | None = None,
    checkpoint_config: CheckpointConfig | None = None,
) -> RecoveryStrategy:
    """Construct the configured :class:`RecoveryStrategy`.

    Args:
        config: Engine recovery configuration discriminator.
        checkpoint_repo: Required when ``config.strategy`` is
            :attr:`RecoveryStrategyType.CHECKPOINT`. May be ``None``
            for the fail-reassign path.
        heartbeat_repo: Optional heartbeat repository forwarded to
            :class:`CheckpointRecoveryStrategy` for sidecar cleanup on
            fallback.
        checkpoint_config: Required when ``config.strategy`` is
            :attr:`RecoveryStrategyType.CHECKPOINT`. Controls
            ``max_resume_attempts`` and related checkpoint behaviour.

    Returns:
        The recovery strategy matching the discriminator.

    Raises:
        RecoveryConfigError: ``config.strategy`` is ``CHECKPOINT`` but
            one of ``checkpoint_repo`` / ``checkpoint_config`` was not
            supplied.
    """
    match config.strategy:
        case RecoveryStrategyType.FAIL_REASSIGN:
            return FailAndReassignStrategy()
        case RecoveryStrategyType.CHECKPOINT:
            if checkpoint_repo is None or checkpoint_config is None:
                msg = (
                    "RecoveryStrategyType.CHECKPOINT requires both "
                    "checkpoint_repo and checkpoint_config to be wired "
                    "through build_recovery_strategy (typically via the "
                    "lifecycle helpers from the active PersistenceBackend)."
                )
                raise RecoveryConfigError(msg)
            from synthorg.engine.checkpoint.strategy import (  # noqa: PLC0415
                CheckpointRecoveryStrategy,
            )

            return CheckpointRecoveryStrategy(
                checkpoint_repo=checkpoint_repo,
                heartbeat_repo=heartbeat_repo,
                config=checkpoint_config,
            )
