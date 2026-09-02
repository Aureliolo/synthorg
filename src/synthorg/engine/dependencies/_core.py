# module-kind: declarative
"""What every engine has regardless of which subsystems a deployment runs.

A leaf on purpose, like every module in this package: the bundles are named
by ``AgentEngine`` and by the assembly that fills them, so defining one
beside either would close a cycle.
"""

from dataclasses import dataclass

from synthorg.core.clock import Clock
from synthorg.engine.loop_protocol import ExecutionLoop, ShutdownChecker
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineCore:
    """The provider, the clock, the tools and the loop.

    ``clock`` carries no default because a hidden ``SystemClock()`` is the
    shape this package exists to remove: a caller that did not choose one
    is indistinguishable from a caller that meant the real clock, and the
    two are different claims in a test.

    Attributes:
        provider: The completion driver this engine dispatches through
            when an agent's own pair resolves to nothing else.
        clock: Time source. Tests inject a fake.
        config_resolver: Live settings reads, or ``None`` when no settings
            service backs this engine (the live security features then
            stay unwired and say so).
        tool_registry: The tools an agent starts from. ``None`` means no
            tool invoker is built at all, so the agent answers in prose.
        execution_loop: An externally supplied loop, or ``None`` to have
            the engine build its own wired with every in-flight control it
            holds. Supplying one takes ownership of that wiring.
        shutdown_checker: Asked at safe boundaries whether the process is
            stopping, or ``None`` when nothing can stop this engine.
    """

    provider: CompletionProvider
    clock: Clock
    config_resolver: ConfigResolver | None
    tool_registry: ToolRegistry | None
    execution_loop: ExecutionLoop | None
    shutdown_checker: ShutdownChecker | None


__all__ = ["EngineCore"]
