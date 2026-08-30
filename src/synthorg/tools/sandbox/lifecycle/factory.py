"""Factory for sandbox lifecycle strategies."""

from collections.abc import Awaitable, Callable

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
    pin_check: Callable[[str], Awaitable[bool]] | None = None,
) -> SandboxLifecycleStrategy:
    """Instantiate a lifecycle strategy from its config discriminator.

    Args:
        config: Lifecycle configuration with the ``strategy`` field.
        clock: Optional clock injected into time-driven strategies
            (``per-agent`` and ``per-task``). When ``None`` each
            strategy's default ``SystemClock`` is used.
        pin_check: Async predicate, keyed by ``container_id``,
            answering whether a live background job is still running
            inside it. Threaded into ``per-agent`` and ``per-task``
            (the two strategies with a persistent container a job could
            outlive its own turn in); ``per-call`` has none, so
            backgrounding is refused before reaching this factory
            rather than accepted and immediately orphaned. ``None``
            (the default) means no background-job feature is wired.

    Returns:
        A concrete ``SandboxLifecycleStrategy`` implementation.

    Raises:
        ValueError: If the strategy name is unrecognised.
    """
    match config.strategy:
        case "per-agent":
            return PerAgentStrategy(config, clock=clock, pin_check=pin_check)
        case "per-task":
            return PerTaskStrategy(clock=clock, pin_check=pin_check)
        case "per-call":
            return PerCallStrategy()
    msg = f"Unknown lifecycle strategy: {config.strategy!r}"  # type: ignore[unreachable]
    raise ValueError(msg)
