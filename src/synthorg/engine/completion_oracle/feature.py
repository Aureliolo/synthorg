# module-kind: feature
"""Completion-oracle feature manifest.

Declares the completion-oracle subsystem's ghost-wired construction seams and
its verdict-archive read surface. The oracle owns no settings namespace or
state slice: it is wired into the engine's completion-gate chain during
startup (``attach_completion_oracle_gates``) and reads the engine's
already-persisted execution records. This manifest also carries the oracle's
dynamically-constructed symbols so they satisfy the ghost-wiring parity gate
alongside the package they belong to, rather than crowding the engine
manifest.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.gate_verdicts import CompletionOracleReportController

FEATURE: FeatureModule = FeatureManifest(
    name="completion_oracle",
    controllers=(CompletionOracleReportController,),
    ghost_wired_symbols=(
        "build_completion_oracle_tool_seed",
        "build_completion_oracle_runtime",
        "CompletionOracleGateService",
        "ReviewerAgentEngineRunner",
        "SubmitCompletionOracleVerdictTool",
        "InMemoryCompletionOracleReportRepository",
        "BuildTestOracle",
        "attach_completion_oracle_gates",
    ),
    depends_on=(),
)
