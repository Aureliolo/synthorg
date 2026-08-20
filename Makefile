# SynthOrg developer task shortcuts.
#
# These wrap the canonical `uv run` invocations documented in CLAUDE.md so
# common workflows are one command. The Makefile is a convenience layer, not a
# source of truth: every target maps to a command you can run directly.

.PHONY: benchmark record-benchmark-scores loop-ab loop-ab-record \
	recursion-depth recursion-depth-record \
	typecheck typecheck-warm typecheck-status typecheck-stop \
	test-durations build-openhands-image \
	dev-up dev-restart dev-status dev-logs dev-down

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
# and the OpenHands image (for that leg), and a `--company-config` whose
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

# Print the recursion-depth matrix and the sessions it would run, without
# spending anything: this path boots no gateway, opens no port and starts no
# container. Run it before `recursion-depth-record`. The session figure is a
# FLOOR, because the real count is a product of branching factors the manifest
# cannot predict, which is why the sweep also carries a hard ceiling.
# See `evals/recursion_depth/manifest.yaml`.
recursion-depth:
	PYTHONPATH=. uv run python scripts/record_recursion_depth.py $(ARGS)

# Measure the recursion-depth sweep for real (REAL PROVIDER SPEND) and rewrite
# the committed chart under `evals/recursion_depth/results/`. Like the A/B
# recorder it hosts its own gateway, so no running API is needed; it does need a
# Docker daemon and a `--company-config` whose `providers:` block aliases the
# manifest's example-* ids to real models, one for the executor and a DIFFERENT
# one for the reviewer (the harness refuses an identical pair, because the gate
# is the treatment and a judge on the executor's own binding biases toward the
# null).
#
# This is a large bill, so stage it. `--depths` narrows the sweep to a subset of
# the manifest's caps and `--max-sessions` lowers the ceiling:
#
#   make recursion-depth-record ARGS="--company-config my-providers.yaml --depths 1,2"
#
# `--keep-workspaces` leaves every unit's tree on disk, which is where the thing
# the sweep actually built ends up.
#
# A sweep runs agent-authored code on this machine: the held-out oracle grades
# the delivered CLI by running it, and each unit's own tests are run against its
# own tree.
recursion-depth-record:
	PYTHONPATH=. uv run python scripts/record_recursion_depth.py --record $(ARGS)

# Build the OpenHands loop image from the working tree, for a record run that
# has to measure local changes under `docker/openhands/`. BASE_IMAGE defaults to
# the published sandbox base, so the layers below the entrypoint match what CI
# builds on; override it to test against a locally built base.
#
# Pinned to this tree's release tag rather than `latest`, which moves: the
# scoreboard stamps only the commit, so two recordings a week apart could
# otherwise differ because of the base image and read as a loop difference.
# Read from pyproject rather than written out, so a release bump carries it.
# `?=` defers the shell to first use, keeping it off every other target.
BASE_IMAGE ?= ghcr.io/aureliolo/synthorg-sandbox:v$(shell sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml)
build-openhands-image:
	docker build -f docker/openhands/Dockerfile --build-arg BASE_IMAGE=$(BASE_IMAGE) -t synthorg-openhands:local .
	@echo "built synthorg-openhands:local; record against it with:"
	@echo "  make loop-ab-record ARGS=\"--company-config <yours> --openhands-image synthorg-openhands:local\""

# ── The local dev arm ────────────────────────────────────────────────────────
#
# Runs the backend where it ships (the Linux image, from this worktree's
# source) instead of natively, and swaps it into the stack the operator is
# already running. Postgres, NATS, web and every secret are untouched, so the
# organisation you run against comes along.
#
# Native execution cannot run a single agent tool on Windows: psycopg's async
# pool requires the SelectorEventLoop, and both `create_subprocess_exec` and
# the Docker named pipe require the ProactorEventLoop, and no process can have
# both. Running the backend in a container removes the class of problem rather
# than the instance, and the arm differs from what an operator receives in
# exactly one respect: whether src/ is baked or mounted.
#
# A Python change costs `make dev-restart` (nothing rebuilds; the source is
# mounted). A dependency change costs `make dev-up`, which rebuilds the venv
# layer. Web changes still hot-reload through the Vite dev server.

# Which stack to overlay. Asked of the daemon rather than configured: the
# daemon knows which file made the running containers, and a second copy of
# that fact is a second thing to keep in step.
SYNTHORG_STACK_CONTAINER ?= synthorg-backend-1

# Declared rather than discovered: the compose file names the project, so it
# is the same on every install and there is nothing to read back off a running
# container.
#
# `override`, because this is a mirror of that declaration rather than a knob.
# Getting it wrong does not fail loudly: `up -d backend` under a name matching
# no running stack quietly stands up a SECOND one, with fresh volumes and an
# empty database, alongside the operator's. An inherited environment value is
# the likeliest way to get it wrong, and `SYNTHORG_STACK_PROJECT=data` is a
# plausible thing to have exported while migrating off the old name.
override SYNTHORG_STACK_PROJECT = synthorg

# The label lists every file the container was created from, comma-separated,
# so once the dev arm is up it names this overlay too. Dropping our own entry
# by EXACT path (a substring match would also drop an operator file that merely
# contains the name) and keeping EVERY file that remains gets back to the
# operator's stack whether the arm is up or not. All of them, because a stack
# made from a base plus overrides carries its backend mounts, environment,
# secrets, ports and image in the later files: restoring from the first alone
# would put a DIFFERENT backend back. Each is emitted pre-quoted as its own
# `-f` so a path containing a space survives the trip into the recipe's shell.
# Compose writes the list without escaping, so a compose path containing a
# comma is already ambiguous by the time it reaches this label; nothing here
# can recover it.
#
# Separators are normalised before the comparison because the two sides do not
# agree on Windows: compose records the label in the form it was invoked with
# (`C:\...`) while `DEV_OVERLAY_FILE` is the `cygpath -m` form (`C:/...`), so an
# exact match against the raw entry never fires and `dev-down` re-applies the
# very overlay it exists to remove. Emitting the normalised form is deliberate
# too: Docker resolves either on Windows, and it is the form the bind sources
# below already use.
#
# The empty-line delete is load-bearing, not tidiness: `docker inspect` writes
# a bare newline to stdout for a container it cannot find or a label that is
# not set, and quoting that would yield a non-empty `-f ''` that reads to
# `require_stack` as a stack it found.
DEV_COMPOSE_ARGS = $(shell docker inspect $(SYNTHORG_STACK_CONTAINER) \
	--format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null \
	| tr ',' '\n' | tr '\\\\' '/' | grep -vxF '$(DEV_OVERLAY_FILE)' \
	| sed -e '/^$$/d' -e "s|^|-f '|" -e "s|$$|'|" | tr '\n' ' ')

# The apko-composed base the backend Dockerfile layers onto. It has no default
# in the Dockerfile on purpose (Scorecard pinned-dependencies), so something
# has to supply it, and the honest source is the stack being overlaid: the
# published backend image carries the build it came from in
# `org.opencontainers.image.version`, and the base is published under the same
# tag. Taking it from there keeps the layers below the venv the operator's
# own, which is what makes this a dev arm rather than a different deployment.
# It resolves a mutable tag rather than the digest that image was built on, so
# it is the same recipe, not a byte-identical guarantee; pass a digest when
# that distinction matters.
#
# Once the dev arm is up, the running image is our own and carries no such
# build, so it records the base it used instead and the second read finds it
# there. Override either to pin a digest or to test another base.
SYNTHORG_STACK_BUILD = $(shell docker inspect $(SYNTHORG_STACK_CONTAINER) \
	--format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null)
SYNTHORG_DEV_BASE = $(shell docker inspect $(SYNTHORG_STACK_CONTAINER) \
	--format '{{index .Config.Labels "io.synthorg.dev.base"}}' 2>/dev/null)
# `strip` is load-bearing: make turns each backslash-newline inside a function
# call into a space, and an image reference with a leading space is not one.
SYNTHORG_BACKEND_BASE_IMAGE ?= $(strip $(if $(SYNTHORG_STACK_BUILD),\
	ghcr.io/aureliolo/synthorg-backend-base:$(SYNTHORG_STACK_BUILD),\
	$(SYNTHORG_DEV_BASE)))

# Docker needs a path IT can resolve as a bind source. Under an MSYS2 make
# `$(CURDIR)` is `/c/Users/...`, which Docker Desktop reads as a path inside
# its Linux VM and answers with an empty directory: /app/src would be mounted
# EMPTY over the image's source and the backend would fail to import, for a
# reason that reads like a broken image. `cygpath -m` yields the `C:/...` form
# it can resolve; on a platform without it the value is already right.
DEV_REPO_ROOT := $(shell cygpath -m '$(CURDIR)' 2>/dev/null || echo '$(CURDIR)')
DEV_OVERLAY_FILE := $(DEV_REPO_ROOT)/docker/compose.dev.yml

# The port the stack actually publishes, not an assumption: `synthorg init
# --backend-port` moves it, and polling the wrong one reports a healthy backend
# unhealthy after two minutes of waiting.
# One line per binding (the container publishes on both IPv4 and IPv6), so the
# first is taken rather than the concatenation of every host port.
SYNTHORG_BACKEND_PORT ?= $(shell docker port $(SYNTHORG_STACK_CONTAINER) 3001/tcp 2>/dev/null \
	| sed -n '1s/.*://p')
DEV_BACKEND_PORT = $(if $(SYNTHORG_BACKEND_PORT),$(SYNTHORG_BACKEND_PORT),3001)
DEV_BACKEND_URL = http://127.0.0.1:$(DEV_BACKEND_PORT)

DEV_COMPOSE = SYNTHORG_REPO_ROOT='$(DEV_REPO_ROOT)' \
	SYNTHORG_BACKEND_PORT='$(DEV_BACKEND_PORT)' \
	SYNTHORG_BACKEND_BASE_IMAGE='$(SYNTHORG_BACKEND_BASE_IMAGE)' \
	docker compose -p '$(SYNTHORG_STACK_PROJECT)' \
	$(DEV_COMPOSE_ARGS) -f '$(DEV_OVERLAY_FILE)'

# Fails loudly rather than inventing a stack: without a running one there is no
# database, no secrets and no organisation to run against, and standing a
# second one up would silently be a different deployment.
define require_stack
	@test -n "$(DEV_COMPOSE_ARGS)" -a -n "$(SYNTHORG_STACK_PROJECT)" || { \
		echo "No running stack found (looked for container '$(SYNTHORG_STACK_CONTAINER)')."; \
		echo "Start one with 'synthorg start', or name the running one:"; \
		echo "  make dev-up SYNTHORG_STACK_CONTAINER=<name>"; \
		exit 2; }
endef

# Separate from require_stack because only a BUILD needs a base image. Folding
# the two together kept `dev-down` (which builds nothing) from running whenever
# derivation failed, which made the one command that restores the verified
# image unreachable exactly when the arm was in a bad state.
define require_base_image
	@test -n "$(SYNTHORG_BACKEND_BASE_IMAGE)" || { \
		echo "The running backend image names neither a published build nor a"; \
		echo "recorded dev base, so the base to layer on cannot be derived."; \
		echo "Pass one explicitly:"; \
		echo "  make dev-up SYNTHORG_BACKEND_BASE_IMAGE=<ref>"; \
		exit 2; }
endef

dev-up:
	$(require_stack)
	$(require_base_image)
	@echo "project     $(SYNTHORG_STACK_PROJECT)"
	@echo "overlaying  $(DEV_COMPOSE_ARGS)"
	@echo "base image  $(SYNTHORG_BACKEND_BASE_IMAGE)"
	$(DEV_COMPOSE) up -d --build backend
	@$(MAKE) --no-print-directory dev-status

dev-restart:
	$(require_stack)
	$(require_base_image)
	$(DEV_COMPOSE) up -d --force-recreate backend
	@$(MAKE) --no-print-directory dev-status

# Waits for the API, then decides whether this arm can execute an agent tool at
# all. That verdict is the point: an arm that cannot spawn a process or reach
# the container backend says so here, rather than many turns into a failed
# agent.
#
# The session is load-bearing, not incidental. `/api/v1/subsystems` sits behind
# `require_read_access` because the set of subsystems a deployment is missing
# describes its topology, so an unauthenticated read is a 401 and the check
# reports nothing exactly when it has something to say. The overlay's own dev
# bypass mints the session, which is the same password-free endpoint the Vite
# frontend uses, so the arm interrogates itself with no credential reaching
# this recipe, and the jar holding the session is removed however the shell
# exits.
#
# `active` is the only passing phase. This subsystem requires nothing, so it
# never rests in `waiting`: it either probed successfully or declined, and
# every phase other than `active` means an agent tool cannot run. A failure
# here is a verdict on capability rather than on the containers, which are up
# either way.
dev-status:
	@for i in $$(seq 1 60); do \
		curl -sf $(DEV_BACKEND_URL)/api/v1/healthz >/dev/null 2>&1 && break; \
		sleep 2; \
	done
	@curl -sf $(DEV_BACKEND_URL)/api/v1/healthz >/dev/null 2>&1 \
		|| { echo "backend did not become healthy; 'make dev-logs' has the reason"; exit 1; }
	@echo "backend healthy on $(DEV_BACKEND_URL)"
	@jar=$$(mktemp); trap 'rm -f "$$jar"' EXIT; \
	curl -sf -X POST -c "$$jar" $(DEV_BACKEND_URL)/api/v1/auth/dev-login >/dev/null 2>&1 || { \
		echo "no dev session on $(DEV_BACKEND_URL), so the agent_tool_execution"; \
		echo "phase cannot be read. Either this is not the dev arm (the bypass is"; \
		echo "gone after 'make dev-down'), or first-run setup has yet to create an"; \
		echo "admin for it to log in as."; \
		exit 1; }; \
	report=$$(curl -sf -b "$$jar" $(DEV_BACKEND_URL)/api/v1/subsystems \
		| grep -o '"name":"agent_tool_execution"[^}]*'); \
	phase=$$(printf '%s' "$$report" | sed -n 's/.*"phase":"\([a-z_]*\)".*/\1/p'); \
	test -n "$$phase" || { \
		echo "the subsystem report names no agent_tool_execution phase;"; \
		echo "'make dev-logs' has the reason"; \
		exit 1; }; \
	echo "agent_tool_execution $$phase"; \
	test "$$phase" = active || { \
		echo "$$report"; \
		echo "this arm cannot execute an agent tool; the containers are up regardless"; \
		exit 1; }

dev-logs:
	$(require_stack)
	$(DEV_COMPOSE) logs -f backend

# Puts the operator's own backend back: same project, same file, without this
# overlay, so the digest-pinned image the CLI verified is what runs again.
# Deliberately does NOT require a base image: this is the command that removes
# the dev auth bypass, and it must work whenever the arm is up, including when
# whatever broke base derivation is why you are running it.
dev-down:
	$(require_stack)
	docker compose -p '$(SYNTHORG_STACK_PROJECT)' $(DEV_COMPOSE_ARGS) up -d --force-recreate backend
	@docker rm -f '$(SYNTHORG_STACK_PROJECT)-dev-init-1' >/dev/null 2>&1 || true
	@echo "operator backend restored; the dev auth bypass is gone"
	@echo "the bytecode cache volume remains: docker volume rm $(SYNTHORG_STACK_PROJECT)_synthorg-devcache"
