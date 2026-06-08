"""Factory for sandbox lifecycle strategies."""

from synthorg.core.clock import Clock
from synthorg.observability import get_logger
from synthorg.tools.sandbox.lifecycle.config import SandboxLifecycleConfig
from synthorg.tools.sandbox.lifecycle.per_agent import PerAgentStrategy
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.lifecycle.per_task import PerTaskStrategy
from synthorg.tools.sandbox.lifecycle.protocol import SandboxLifecycleStrategy

logger = get_logger(__name__)


def create_lifecycle_strategy(
    config: SandboxLifecycleConfig,
    *,
    clock: Clock | None = None,
) -> SandboxLifecycleStrategy:
    """Instantiate a lifecycle strategy from its config discriminator.

    Args:
        config: Lifecycle configuration with the ``strategy`` field.
        clock: Optional clock injected into time-driven strategies
            (currently ``per-agent``). When ``None`` the strategy's
            default ``SystemClock`` is used.

    Returns:
        A concrete ``SandboxLifecycleStrategy`` implementation.

    Raises:
        ValueError: If the strategy name is unrecognised.
    """
    match config.strategy:
        case "per-agent":
            return PerAgentStrategy(config, clock=clock)
        case "per-task":
            return PerTaskStrategy()
        case "per-call":
            return PerCallStrategy()
    msg = f"Unknown lifecycle strategy: {config.strategy!r}"  # type: ignore[unreachable]
    raise ValueError(msg)
