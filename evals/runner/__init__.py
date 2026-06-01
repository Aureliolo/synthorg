"""Golden-company benchmark runner.

Boots a SynthOrg company per brief, runs the brief through the agent engine
with a deterministic provider, captures process-fact events, grades the
deliverable, and assembles a :class:`evals.models.scorecard.Scorecard`.

The public entry point is :func:`evals.run.run_benchmark`; this package holds
the orchestration internals (per-brief execution, grading dispatch).
"""
