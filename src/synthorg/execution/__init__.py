"""Execution-trajectory data models, shared as a dependency-free leaf.

Holds the per-turn record, trajectory enums, efficiency ratios, and a
structural view protocol for an execution result. These are pure data
shapes (and one ``Protocol``) that the engine produces and that
non-engine subsystems (e.g. ``budget.coordination_collector``, memory
distillation, analytics tools) consume.

They live here, OUTSIDE the ``engine`` package, because importing any
``engine`` submodule runs the heavy ``engine/__init__`` (which loads the
whole orchestrator stack). A consumer that only needs ``TurnRecord`` or
the ``ExecutionResultView`` shape must be able to import it from a leaf
without triggering that cascade, so it stays cold-importable and free of
the engine import cycle. ``engine.loop_protocol`` depends on this leaf
(downward), not the other way round.

This package uses explicit per-module imports; import from the defining
submodule, e.g. ``from synthorg.execution.turn import TurnRecord``.
"""
