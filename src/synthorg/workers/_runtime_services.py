# module-kind: code
"""The :class:`RuntimeServices` DTO built behind the provider switch.

Extracted from :mod:`runtime_builder` so the result type is importable
without pulling in the whole orchestrator, and so the orchestrator stays
under its module-size budget.
"""

from typing import NamedTuple

from synthorg.engine.completion_oracle.builder import CompletionOracleRuntime
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.security.redteam.builder import RedTeamRuntime
from synthorg.security.visionverify.protocol import VisionVerifierGate
from synthorg.tools.state import WebResearchTools
from synthorg.workers.execution_service import WorkerExecutionService


class RuntimeServices(NamedTuple):
    """The runtime services built behind the provider switch.

    INVARIANT (enforced by construction in
    :func:`synthorg.workers.runtime_builder.build_runtime_services`, not by
    the type): when ``coordinator`` is not ``None`` it and
    ``worker_execution_service`` share the *same* boot
    :class:`AgentEngine` instance, so worker tasks and coordinator
    sub-agents observe one interrupt store, event-stream hub, and clock
    seam. The ``work_pipeline`` (when not ``None``) holds those very
    ``worker_execution_service`` and ``coordinator`` instances plus a
    single shared :class:`AgentTaskScorer`, so solo and team routing
    never diverge. A divergent engine would split agent state silently;
    ``tests/unit/workers/test_runtime_builder.py`` asserts the identity.
    ``coordinator`` and ``work_pipeline`` are ``None`` only in the
    empty-company (no-provider) case, where ``worker_execution_service``
    is a :class:`NoProviderExecutionService`; ``work_pipeline`` is also
    ``None`` when no intake runtime is wired (no work entry path).
    ``red_team_runtime`` is ``None`` when the adversarial gate is
    disabled (default) OR when no provider is configured.
    ``completion_oracle_runtime`` is ``None`` only when
    ``engine.completion_oracle_enabled`` is off (it defaults ON) or no
    provider is configured; the build/test gate still attaches whenever the
    oracle is enabled, since it needs no provider.
    """

    worker_execution_service: WorkerExecutionService
    coordinator: MultiAgentCoordinator | None
    work_pipeline: WorkPipeline | None
    red_team_runtime: RedTeamRuntime | None = None
    completion_oracle_runtime: CompletionOracleRuntime | None = None
    completion_oracle_enabled: bool = False
    vision_gate: VisionVerifierGate | None = None
    # Defaults to neither installed, which is what the two early returns above
    # the tool-registry build actually produce. A default of "installed" would
    # make every path that never reached the build claim it had.
    web_research: WebResearchTools = WebResearchTools()
