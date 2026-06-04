# SynthOrg developer task shortcuts.
#
# These wrap the canonical `uv run` invocations documented in CLAUDE.md so
# common workflows are one command. The Makefile is a convenience layer, not a
# source of truth: every target maps to a command you can run directly.

.PHONY: benchmark record-benchmark-scores

# Run the golden-company benchmark twice and emit scorecards: the reference
# company at the COMPETENT profile (its executable brief passes its hidden
# tests) and the broken company at the DEGRADED profile (the executable brief
# compiles but fails its hidden tests). The broken run is expected to score
# worse and exit non-zero, so its line is prefixed with `-` to let the target
# finish. Compare .benchmark/reference and .benchmark/broken scorecards: the gap
# is part budget penalty (broken's starved per-run ceiling) and part a genuine,
# grader-measured quality delta (the executable brief's hidden-test result).
benchmark:
	uv run python -m evals \
		--company-config evals/baselines/reference.yaml \
		--profile competent \
		--out-dir .benchmark/reference
	-uv run python -m evals \
		--company-config evals/baselines/broken.yaml \
		--profile degraded \
		--out-dir .benchmark/broken

# Record (or replay) per-model benchmark cassettes and regenerate the committed
# measured-score seed artifact `src/synthorg/budget/benchmark_seed.json`. The
# default run replays the committed cassettes (deterministic, offline) and
# refuses any model whose cassette is missing. Pass `ARGS=--record` (with real
# provider credentials, the example-* ids aliased to real models) to record the
# cassettes first. See `evals/benchmark_scores/models.yaml`.
record-benchmark-scores:
	uv run python scripts/record_benchmark_scores.py $(ARGS)
