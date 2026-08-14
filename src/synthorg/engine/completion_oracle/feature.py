# module-kind: feature
"""Completion-oracle feature manifest.

Declares the completion-oracle subsystem's ghost-wired construction
seams. The oracle owns no settings namespace, state slice, or REST
controllers of its own: it is wired into the engine's completion-gate
chain during startup (``attach_completion_oracle_gates``) and reads the
engine's already-persisted execution records. This manifest exists so
the oracle's dynamically-constructed symbols satisfy the ghost-wiring
parity gate alongside the package they belong to, rather than crowding
the engine manifest.
"""

from synthorg._core.features import FeatureManifest, FeatureModule

FEATURE: FeatureModule = FeatureManifest(
    name="completion_oracle",
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
