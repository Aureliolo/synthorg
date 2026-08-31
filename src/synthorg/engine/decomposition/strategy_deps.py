# module-kind: declarative
"""What a decomposition strategy is configured and wired with.

Two declarations, kept together in a leaf because they answer one question
between them: the session's own bounds (:class:`AgentSessionDecompositionConfig`)
and the collaborators either strategy is handed
(:class:`DecompositionStrategyDeps`).

They live here rather than beside the strategy that reads them because the
coordinator factory names both while building it, and a strategy importing its
own dependency bundle from the module that constructs it is a cycle. A leaf
importing neither keeps the strategy, the factory and the worker assembly all
naming one declaration.
"""

from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.session_budget import SessionCeilings
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock
from synthorg.engine.agent_state_recording import AgentStateRepositoryProvider
from synthorg.engine.decomposition.progress_protocol import (
    DecompositionProgressReporter,
)
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.engine.errors import DecompositionUnwiredError
from synthorg.engine.loop_protocol import ShutdownChecker
from synthorg.engine.stagnation.models import StagnationDetectionConfig
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.providers.protocol import ProviderSelector
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

#: Refusal for the one dependency the bundle cannot type as required. Worded
#: here, beside the accessor that raises it, so the factory and the registry
#: cannot drift into two accounts of the same wiring fault.
_NO_SELECTOR_MESSAGE: Final[str] = (
    "The owner-run agent-session decomposition requires a provider_selector: "
    "each owner dispatches on its own bound (provider, model), never a shared "
    "default. The single-shot 'llm' strategy needs no selector."
)

#: Fallback for a config built without resolved settings; the operator-facing
#: default lives on ``coordination.decomposition_agent_cost_ceiling``.
_DEFAULT_CEILINGS: Final[SessionCeilings] = SessionCeilings(
    cost_ceiling=2.0, token_ceiling=0
)


class AgentSessionDecompositionConfig(BaseModel):
    """Configuration for the agent-session decomposition strategy.

    Sampling is deliberately absent: it belongs to the bound model rather than
    to the strategy, so the planning session reads it off the owner's own
    binding (:func:`synthorg.engine.agent_sampling.binding_sampling`), exactly
    as a work session does. A field here would be a second answer that could
    not be a right one, since a strategy config does not know which model is
    bound and the value a vendor publishes is a property of that model.

    Attributes:
        max_turns: Hard turn cap for the planning session.
        ceilings: Both spend bounds on the planning session. One field, not
            two, so a wiring path that resolves the money bound cannot leave
            the token bound at its default in silence: money measures nothing
            against a provider that bills by flat subscription, where cost
            never rises.
        memory_digest_budget: Token cap for the org/retro digest spliced into
            the planning brief.
        stagnation: Which intra-loop stagnation detector the planning session
            runs. It travels HERE, with the other session bounds, rather than
            as a loose constructor argument, for the reason ``ceilings`` gives:
            a wiring path that resolves some of them cannot leave the rest at
            their defaults in silence. The deployment's ``config.stagnation``
            remains the one owner of the VALUE; this is a second reader of it,
            because the work loop and the planning loop are different loops and
            each needs its own detector instance.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_turns: int = Field(default=12, ge=1, le=50, description="Planning turn cap")
    ceilings: SessionCeilings = Field(
        default=_DEFAULT_CEILINGS,
        description="Per-session money + token bounds",
    )
    memory_digest_budget: int = Field(
        default=1000,
        ge=0,
        description="Token cap for the org/retro memory digest injected into "
        "the planning brief; 0 injects nothing (the tool grant still applies)",
    )
    stagnation: StagnationDetectionConfig = Field(
        default_factory=StagnationDetectionConfig,
        description="Intra-loop stagnation detector for the planning session",
    )


@dataclass(frozen=True, slots=True)
class DecompositionStrategyDeps:
    """Everything decomposition is wired with beyond its model.

    One object rather than a kwarg per dependency because the strategy
    registry passes a UNIFORM set to every builder, so each new dependency
    widened three signatures at once and each builder then had to ``del`` the
    ones it does not use. Bundled, a dependency the single-shot path ignores
    costs it nothing, and every signature involved stays inside the argument
    cap that a loose kwarg had already pushed three of them past.

    Attributes:
        provider_selector: Dispatches each owner on its own bound
            ``(provider, model)``. Required by the agent-session strategy,
            which never borrows a shared default; the single-shot strategy
            needs none.
        tool_provider: Supplies the read-only planning tools the owner-run
            session is granted. ``None`` grants the terminal submit tool
            alone.
        cost_tracker: What both strategies attribute their planning spend to.
            NOT agent-session-only: the single-shot strategy opens its own
            ``cost_recording_scope`` around the planning call, so dropping it
            leaves every decomposition that path runs attributed to nothing.
        shutdown_checker: Lets the planning session halt at a turn boundary
            once a graceful shutdown begins. Derived by the coordinator
            factory from the shutdown manager it already holds, so a value set
            here is replaced on that path.
        agent_session_config: Operator-tuned turn cap, spend bounds, memory
            digest budget and stagnation thresholds for the planning loop.
            ``None`` uses the strategy defaults.
        planning_memory: Pre-seeds the org/retro digest into the planning
            brief so the plan carries prior learnings even when the owner
            never calls the recall tool.
        config_resolver: Read once per call for the output-token ceiling, so
            a raised ceiling applies to the next decomposition rather than the
            next rebuild.
        agent_states: Resolves the live agent-state repository at call time.
            The planning session claims a row for its duration, because a
            session running as a roster agent IS the org working and every
            surface answering that question reads the live rows; without it an
            hour of planning shows as an idle org. ``None`` (or a provider
            answering ``None``) plans without recording liveness.
        progress_reporter: Where the SERVICE publishes how far the tree has
            got, so a plan that is ``PLANNING`` with no items can say whether
            it is working. One of the two dependencies here belonging to the
            service rather than to a strategy, and it travels with the rest
            because a wiring path resolving some of these cannot leave the
            others at their defaults in silence. ``None`` decomposes without
            reporting.
        clock: What stamps each progress snapshot. The other service-owned
            dependency, and it travels beside the reporter because it is only
            READ through it: a service left on its own ``SystemClock`` while
            the deployment runs on another is a snapshot timestamped off a
            clock nothing else in the process agrees with, and the timestamp
            is the whole of how a working decomposition is told from a hung
            one. ``None`` uses the system clock.
    """

    provider_selector: ProviderSelector | None = None
    tool_provider: DecompositionToolProvider | None = None
    cost_tracker: CostTrackerProtocol | None = None
    shutdown_checker: ShutdownChecker | None = None
    agent_session_config: AgentSessionDecompositionConfig | None = None
    planning_memory: MemoryInjectionStrategy | None = None
    config_resolver: ConfigResolverProtocol | None = None
    agent_states: AgentStateRepositoryProvider | None = None
    progress_reporter: DecompositionProgressReporter | None = None
    clock: Clock | None = None

    def require_provider_selector(self) -> ProviderSelector:
        """Return the selector, refusing the one field that is not optional.

        Every field here is typed optional because the registry builds each
        strategy from one uniform bundle, and the single-shot strategy needs
        no selector. The agent-session strategy does, so the requirement is
        this accessor rather than the type: named once, it cannot become two
        guards that drift into two accounts of the same wiring fault.

        Returns:
            The bound provider selector.

        Raises:
            DecompositionUnwiredError: No selector was wired.
        """
        if self.provider_selector is None:
            raise DecompositionUnwiredError(_NO_SELECTOR_MESSAGE)
        return self.provider_selector
