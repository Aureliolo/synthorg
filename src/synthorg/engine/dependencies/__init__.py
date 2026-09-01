# module-kind: declarative
"""Everything an :class:`~synthorg.engine.agent_engine.AgentEngine` is wired with.

ONE required argument, and every field of every bundle required with it.
The point is not tidiness: it is that a partially wired engine must not be
constructable.

The shape this replaces took 64 keyword arguments, 63 of them defaulting to
``None``. A deployment supplied 51 and the harness recording this loop
supplied 8, for eight recordings, silently, and nothing at any layer could
tell. Omitting ``compaction_callback`` looked exactly like deciding against
it. So the corpus that produced a root-cause analysis of the loop had
measured an engine the product does not ship.

Absence is still allowed and still common. What is no longer allowed is
absence by OMISSION: a caller writes ``compaction_callback=None`` and that
is a decision a reader can see and a reviewer can question, where a missing
keyword is a decision nobody made.

Grouped rather than flat because the groups are the ones the composition
root already reads from (``BudgetStateSlice``, ``SecurityStateSlice``,
``MemoryStateSlice``, ...), and because a single 64-field literal at each
call site is a list nobody checks.
"""

from dataclasses import dataclass

from synthorg.engine.dependencies._behaviour import EngineBehaviour
from synthorg.engine.dependencies._budget import EngineBudget
from synthorg.engine.dependencies._core import EngineCore
from synthorg.engine.dependencies._governance import EngineGovernance
from synthorg.engine.dependencies._loop_controls import EngineLoopControls
from synthorg.engine.dependencies._memory import EngineMemory
from synthorg.engine.dependencies._observability import EngineObservability
from synthorg.engine.dependencies._org import EngineOrg
from synthorg.engine.dependencies._recovery import CheckpointWiring, EngineRecovery
from synthorg.engine.dependencies._routing import EngineRouting
from synthorg.engine.dependencies._tooling import EngineTooling


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineDependencies:
    """The whole wiring of one engine, declared.

    Attributes:
        core: The provider, the clock, the tools and the loop.
        routing: How an agent's own pair reaches a driver.
        budget: What measures and bounds spend.
        governance: Approval, policy, audit and the review gates.
        loop_controls: What runs between turns.
        memory: What an agent recalls, and what it learns.
        org: The roster, the board, and the MCP surface.
        tooling: What extends the base tool registry per task.
        observability: Where progress and failure are published.
        recovery: What happens when a run does not finish.
        behaviour: Operator switches on what an agent may do.
    """

    core: EngineCore
    routing: EngineRouting
    budget: EngineBudget
    governance: EngineGovernance
    loop_controls: EngineLoopControls
    memory: EngineMemory
    org: EngineOrg
    tooling: EngineTooling
    observability: EngineObservability
    recovery: EngineRecovery
    behaviour: EngineBehaviour


__all__ = [
    "CheckpointWiring",
    "EngineBehaviour",
    "EngineBudget",
    "EngineCore",
    "EngineDependencies",
    "EngineGovernance",
    "EngineLoopControls",
    "EngineMemory",
    "EngineObservability",
    "EngineOrg",
    "EngineRecovery",
    "EngineRouting",
    "EngineTooling",
]
