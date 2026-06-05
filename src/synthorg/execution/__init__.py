"""Execution-trajectory data models, free of any ``engine`` dependency.

Holds the per-turn record, trajectory enums, efficiency ratios, and a
structural view protocol for an execution result. These are pure data
shapes (and one ``Protocol``) that the engine produces and that
non-engine subsystems (e.g. ``budget.coordination_collector``, memory
distillation, analytics tools) consume.

They live here, OUTSIDE the ``engine`` package, because importing any
``engine`` submodule runs the heavy ``engine/__init__`` (which loads the
whole orchestrator stack). A consumer that only needs ``TurnRecord`` or
the ``ExecutionResultView`` shape must be able to import it without
triggering that cascade, so this leaf never imports ``engine``;
``engine.loop_protocol`` depends on it (downward), not the other way
round. The leaf is not dependency-free in general: ``turn`` imports
``providers.enums`` and ``budget.call_category`` for its field types, so a
consumer importing ``execution.turn`` still pulls those hubs. The
guarantee this leaf provides is the absence of an ``engine`` edge (which
is what closed the cold-import cycle), not zero transitive dependencies.

This package uses explicit per-module imports; import from the defining
submodule, e.g. ``from synthorg.execution.turn import TurnRecord``.
"""
