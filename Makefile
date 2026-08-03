# SynthOrg developer task shortcuts.
#
# These wrap the canonical `uv run` invocations documented in CLAUDE.md so
# common workflows are one command. The Makefile is a convenience layer, not a
# source of truth: every target maps to a command you can run directly.

.PHONY: benchmark record-benchmark-scores loop-ab loop-ab-record \
	typecheck typecheck-warm typecheck-status typecheck-stop \
	test-durations

# Type-check the tree through the mypy daemon (seconds once warm, and the same
# command the pre-push hook runs). `typecheck-warm` pays the one-time graph
# build up front so the first push of a session is not the slow one; it blocks
# for several minutes. A warm daemon holds ~2.5GB resident per worktree, so
# `typecheck-stop` reclaims it when moving to another branch. `typecheck-status`
# reports what is running and how much it costs.
#
# A daemon also expires on its own after two hours idle (see
# `_DAEMON_IDLE_TIMEOUT_SECONDS` in run_affected_mypy.py for why it has to).
# dmypy can only bind that when the daemon starts, so one already running
# without it is restarted once to adopt it, at the cost of a single graph
# rebuild. `typecheck-stop` is still how to reclaim a daemon now rather than in
# two hours, and is required before removing a worktree.
#
# When a worktree still will not delete, `--find-holders <path>` lists what is
# holding it (read-only) and `--stop-holder <pid>` terminates one named
# process. Two steps on purpose: nothing discovers and kills in one go.
typecheck:
	uv run python scripts/run_affected_mypy.py

typecheck-warm:
	uv run python scripts/run_affected_mypy.py --warm

typecheck-status:
	uv run python scripts/run_affected_mypy.py --status

typecheck-stop:
	uv run python scripts/run_affected_mypy.py --stop

# Rebuild `.test_durations.unit` from a finished CI run's four shard reports,
# so pytest-split keeps partitioning the unit arm by cost rather than by test
# count. Refresh when the four `Test Unit (shard N)` wall-clocks drift apart;
# on a balanced file they land within a few percent of each other.
#
#     make test-durations RUN_ID=30708023122
#
# The timings come from the runners rather than a developer machine, which is
# what makes them comparable to the budget the shards are measured against.
test-durations:
	@test -n "$(RUN_ID)" || { echo "RUN_ID=<ci-run-id> is required"; exit 2; }
	rm -rf .test-durations-reports
	gh run download $(RUN_ID) --dir .test-durations-reports \
		-n test-results-unit-1 -n test-results-unit-2 \
		-n test-results-unit-3 -n test-results-unit-4
	uv run python scripts/generate_test_durations.py \
		--out .test_durations.unit \
		.test-durations-reports/test-results-unit-*/junit-unit-*.xml
	rm -rf .test-durations-reports

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
#
# `PYTHONPATH=.` because `evals` is out-of-package: running a file under
# `scripts/` puts that directory on the path, not the repository root.
record-benchmark-scores:
	PYTHONPATH=. uv run python scripts/record_benchmark_scores.py $(ARGS)

# Print the inner-loop A/B matrix and the number of runs it would execute,
# without spending anything: this path boots no gateway, opens no port and
# starts no container. Run it before `loop-ab-record` to see the size of the
# bill. See `evals/loop_ab/manifest.yaml`.
loop-ab:
	PYTHONPATH=. uv run python scripts/record_loop_ab.py $(ARGS)

# Measure the inner-loop A/B for real (REAL PROVIDER SPEND) and rewrite the
# committed scoreboard under `evals/loop_ab/scoreboard/`. The recorder hosts its
# own gateway, so no running API is needed; what it does need is a Docker daemon
# and the OpenHands image (for the fourth leg), and a `--company-config` whose
# `providers:` block aliases the manifest's example-* ids to real models. The
# default config carries no providers at all, so a record run must supply one:
#
#   make loop-ab-record ARGS="--company-config my-providers.yaml"
#
# `--openhands-image` records against a locally built image. It is REQUIRED
# after any change under `docker/openhands/`: the entrypoint is baked into the
# image, and the default setting names a published tag, so a run without it
# silently measures the previously published entrypoint against real spend.
# Build one with `make build-openhands-image` and pass the tag it prints.
#
# Other flags: `--bind-host` overrides the resolved listener address (unset
# resolves the narrowest one the sandbox can reach), `--bind-port` pins the port
# instead of taking an ephemeral one, `--container-host` overrides the alias the
# sandbox addresses the recorder by, and `--keep-workspaces` leaves each cell's
# tree on disk to inspect instead of reclaiming it.
#
# ARGS is word-split by the shell, which is what lets it carry several flags;
# quote any value containing a space within it, e.g.
# ARGS="--company-config 'my config.yaml'".
#
# Re-run this whenever loop behaviour changes; the scoreboard stamps the commit
# it was measured against, so a stale one is self-evident.
loop-ab-record:
	PYTHONPATH=. uv run python scripts/record_loop_ab.py --record $(ARGS)

# Build the OpenHands loop image from the working tree, for a record run that
# has to measure local changes under `docker/openhands/`. BASE_IMAGE defaults to
# the published sandbox base, so the layers below the entrypoint match what CI
# builds on; override it to test against a locally built base.
BASE_IMAGE ?= ghcr.io/aureliolo/synthorg-sandbox:latest
build-openhands-image:
	docker build -f docker/openhands/Dockerfile --build-arg BASE_IMAGE=$(BASE_IMAGE) -t synthorg-openhands:local .
	@echo "built synthorg-openhands:local; record against it with:"
	@echo "  make loop-ab-record ARGS=\"--company-config <yours> --openhands-image synthorg-openhands:local\""
