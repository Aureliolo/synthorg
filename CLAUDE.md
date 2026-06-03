# CLAUDE.md: SynthOrg

Framework for synthetic organisations (autonomous AI agents orchestrated as a virtual company). Python 3.14+. BUSL-1.1 → Apache 2.0 after Change Date. Layout: `src/synthorg/` (src layout), `tests/`, `web/` (React 19), `cli/` (Go binary), `evals/` (golden-company benchmark, out-of-package).

Web: see `web/CLAUDE.md`. CLI: see `cli/CLAUDE.md` (use `go -C cli`, never `cd cli`). Shell: see `~/.claude/rules/common/bash.md` (canonical for `cd` / `git -C` / Bash file-write rules).

## MANDATORY one-liners

- **Design Spec (MANDATORY)**: read `docs/design/` page before implementing; deviations need approval. See [DESIGN_SPEC.md](docs/DESIGN_SPEC.md).
- **Planning (MANDATORY)**: present every plan for accept/deny before coding.
- **Web Dashboard Design System (MANDATORY)**: reuse `web/src/components/ui/`; design tokens only. Detail in `web/CLAUDE.md`.
- **Regional Defaults (MANDATORY)**: no region/currency/locale privileged; metric units; British English. See [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md).
- **Persistence Boundary (MANDATORY)**: only `src/synthorg/persistence/` may import sqlite/psycopg or emit raw SQL. New repository protocols inherit one or more generic categories from `persistence/_generics.py` (`SingletonRepository` / `IdKeyedRepository` / `FilteredQueryRepository` / `AppendOnlyRepository` / `StatefulRepository` / `MVCCRepository`); bespoke methods are permitted only under [ADR-0001](docs/decisions/0001-repository-protocol-consolidation.md) D7 (real perf optimisation or domain invariant callers must not bypass). Protocols stay `@runtime_checkable`. See [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md).
- **Convention Rollout (MANDATORY)**: every convention PR ships its enforcement gate. See [docs/reference/convention-gates.md](docs/reference/convention-gates.md).
- **Configuration Precedence (MANDATORY)**: DB > env > code default via `SettingsService`/`ConfigResolver` (Cat-1) or env > code default (Cat-2, `read_only_post_init`); Cat-3 bootstrap secrets are pure env at the boot site. YAML is a company-template ingestion format, not a precedence tier. No `os.environ.get` outside startup; pre-init Cat-2 reads use `settings.bootstrap_resolver.resolve_init_value`. See [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md).
- **No Hardcoded Values (MANDATORY)**: numerics live in `settings/definitions/`; allowlist 0/1/-1, HTTP codes, hex masks, powers-of-2, and module-level annotated named constants of the form `NAME: int|float|Final|Final[int]|Final[float] = literal`. Enforced by `scripts/check_no_magic_numbers.py`.
- **Doc Numeric Claims (MANDATORY)**: numerics in README + public docs sourced from `data/runtime_stats.yaml` via `<!--RS:NAME-->` markers. See `data/README.md`.
- **Test Regression (MANDATORY)**: timeout/slow failures = source-code regression; never edit `tests/baselines/unit_timing.json` or any `scripts/*_baseline.{txt,json}` / `scripts/_*_baseline.py`. Both families are PreToolUse-blocked. Per-invocation bypass for gate baselines: `ALLOW_BASELINE_GROWTH=1 git commit ...` (requires explicit user approval).
- **Post-Implementation + Pre-PR Review (MANDATORY)**: after issue: branch + commit + push (no auto-PR); use `/pre-pr-review` (`gh pr create` is blocked by `scripts/check_no_pr_create.sh`). After PR: `/aurelio-review-pr` for external feedback. Fix EVERYTHING valid; no deferring.
- **Module-Size Budget (MANDATORY)**: tiered LOC caps per `# module-kind:` header on the first non-blank/non-shebang/non-encoding line: `controller` 400, `service`/`orchestrator` 600, `complex_service` 1100 (audit-verdict tier; reserved for #2052-style cohesion verdicts, not a free opt-in), `repository` 500, `adapter`/`integration` 700, `feature` 100, `code` 500 (default), `tests` 800, `declarative` exempt, `generated` glob-exempt. Existing offenders baselined in `scripts/_module_size_baseline.json`; no file may grow past its baseline. Allowlisted god-modules (`core/enums.py`, `observability/events/persistence.py`) must net-shrink; the five `api/` entries drained once the controller decomposition brought them under their tier caps. Enforced by `check_module_size_budget.py` + `check_no_growth_in_god_modules.py`. See [docs/decisions/0006-tiered-module-size-policy.md](docs/decisions/0006-tiered-module-size-policy.md).
- **Import Layering + Architecture Drift (MANDATORY)**: declarative `.importlinter` contracts (forbidden-only, direct-imports, blessed back-edges; NO total-order layers) enforced by `lint-imports` (pre-push + CI), alongside the 3 retained custom AST gates (raw-SQL boundary, DTO-leak, dependency-inversion). Graph-level smells (fan-in >=30, LCOM4, budget-pressure within 20% of tier cap) gated by `check_architecture_drift.py` vs the committed `data/architecture_report.json` (regenerate via `scripts/architecture_report.py`; the gate never writes it). See [ADR-0009](docs/decisions/0009-import-layering-contracts.md) + [ADR-0011](docs/decisions/0011-architectural-feedback-loop.md) + [import-layering.md](docs/reference/import-layering.md).

## Quick Commands

```bash
uv sync                                             # all deps
uv sync --group docs                                # docs toolchain (zensical + D2)
bash scripts/install_cli_tools.sh                   # one-time per-machine: golangci-lint + lychee + vale (CI installs separately; install d2 via docs/getting_started.md)
uv run ruff check src/ tests/ --fix                 # lint + auto-fix
uv run ruff format src/ tests/                      # format
uv run mypy --num-workers=4 src/ tests/             # strict type-check
uv run python -m pytest tests/ -m unit                                              # -n 8 --dist=loadfile via pyproject addopts
uv run python -m pytest tests/ -m integration
uv run python -m pytest tests/ -m e2e
uv run python -m pytest tests/ --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80
uv run python -m pytest tests/benchmarks/ --codspeed -n0
HYPOTHESIS_PROFILE=dev uv run python -m pytest tests/ -m unit -k properties
HYPOTHESIS_PROFILE=fuzz uv run python -m pytest tests/ -m unit --timeout=0
bash scripts/install_git_hooks.sh                   # one-time per clone: wire core.hooksPath -> scripts/git-hooks (NOT pre-commit install)
uv run pre-commit run --all-files
uv run pre-commit run lychee --hook-stage pre-push --all-files                      # local Markdown link-check (lychee, internal links only / offline)
vale README.md CLAUDE.md cli/CLAUDE.md web/CLAUDE.md docs/                          # prose linter (Google style + British vocab, sub-second)
uv run python scripts/check_schema_drift_revisions.py --backend sqlite  # or --backend postgres
PYTHONPATH=. uv run zensical build                  # docs
```

## Reference (load on demand)

- [docs/reference/claude-reference.md](docs/reference/claude-reference.md): Doc layout, Docker, releasing, CI, dependencies, Hypothesis deep-dive
- [docs/reference/conventions.md](docs/reference/conventions.md): repository CRUD, lifecycle, response wrapping, validators, event imports, domain errors, file structure, frozen ConfigDict, args models, Pydantic v2, async, Clock seam, observability event-name inventory, repository CRUD method names, MCP handler logging centralisation, repository file structure, registering MANDATORY rules, `activate_*` / `deactivate_*` lifecycle naming
- [docs/reference/convention-gates.md](docs/reference/convention-gates.md): full gate inventory (enforcement gates + meta-gate + PreToolUse hooks)
- [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md), [persistence-boundary.md](docs/reference/persistence-boundary.md), [configuration-precedence.md](docs/reference/configuration-precedence.md), [errors.md](docs/reference/errors.md), [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md), [lifecycle-sync.md](docs/reference/lifecycle-sync.md), [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md), [typed-boundaries.md](docs/reference/typed-boundaries.md), [retry-patterns.md](docs/reference/retry-patterns.md), [scaffolding.md](docs/reference/scaffolding.md), [audit-category-gate-coverage.md](docs/reference/audit-category-gate-coverage.md), [dead-api-endpoints.md](docs/reference/dead-api-endpoints.md), [pluggable-subsystems.md](docs/reference/pluggable-subsystems.md), [protocols-audit.md](docs/reference/protocols-audit.md), [telemetry.md](docs/reference/telemetry.md)

## Diagrams

`d2` for architecture / nested containers, `mermaid` for flowcharts / sequence / pipelines. Markdown tables for tabular data. D2 theme 200 (Dark Mauve), D2 CLI pinned to v0.7.1 in CI.

## Code conventions (detail in [conventions.md](docs/reference/conventions.md))

- Comments WHY only; no reviewer citations / issue back-refs / migration framing. Enforced by `check_no_review_origin_in_code.py` + `check_no_migration_framing.py`.
- No `from __future__ import annotations` (3.14 has PEP 649). PEP 758 except: `except A, B:` no parens unless binding.
- Type-only imports go at module level (ruff TC001/2/3 disabled) so typeguard can resolve annotations at runtime; `if TYPE_CHECKING:` is reserved for genuine import-cycle breakers.
- Type hints on public functions; mypy strict. Google-style docstrings. Line length 88; functions <50 lines. File-size: see the MANDATORY Module-Size Budget paragraph above (tiered per `# module-kind:` header).
- Errors: `<Domain><Condition>Error` from `DomainError`; never inherit `Exception`/`RuntimeError`/etc directly. Enforced by `check_domain_error_hierarchy.py`.
- Pydantic v2 frozen + `extra="forbid"` on every frozen model project-wide (`src/synthorg/` AND `tests/`; gate `check_frozen_model_extra_forbid.py`; `@computed_field` auto-exempt, per-line `# lint-allow: frozen-extra-forbid -- <reason>` for `extra="allow"`/`"ignore"` boundaries); `@computed_field` for derived; `NotBlankStr` for identifiers.
- Args models at every system boundary; `parse_typed()` for every external dict ingestion. Enforced by `check_boundary_typed.py`.
- Immutability: `model_copy(update=...)` or `copy.deepcopy()`; deepcopy at system boundaries.
- Async: `asyncio.TaskGroup` for fan-out/fan-in; helpers catch `Exception` (re-raise `MemoryError`/`RecursionError`).
- Clock seam: `clock: Clock | None = None`; tests inject `FakeClock`. Lifecycle: services own `_lifecycle_lock`; timed-out stops mark unrestartable.
- Untrusted content (SEC-1): `wrap_untrusted()` from `engine.prompt_safety`; `HTMLParseGuard` for HTML.
- Repository CRUD: `save(entity)`, `get(id)`, `delete(id) -> bool`, `list_items(...)`, `query(...)` returning tuples.
- Datetime in persistence: `parse_iso_utc` / `format_iso_utc` from `persistence._shared` (reject naive); `normalize_utc` for already-typed.

## Logging (detail in [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md))

- `from synthorg.observability import get_logger`; variable always `logger`. Never `import logging` / `print()` in app code.
- Event names from `observability.events.<domain>` constants; structured kwargs (`logger.info(EVENT, key=value)`).
- Error paths log WARNING/ERROR with context before raising; state transitions log INFO via `*_STATUS_TRANSITIONED` AFTER persistence write.
- Sink pipeline (level + event filtering): `synthorg.log` excludes routine HTTP-request events; `debug.log` pins specific events to exact levels. See [`.claude/skills/analyse-logs/SKILL.md`](.claude/skills/analyse-logs/SKILL.md) for `SINK_EVENT_EXCLUDES` / `SINK_EXACT_LEVELS` when correlating across logs.
- **Secret-log redaction (SEC-1)**: never `error=str(exc)` or interpolate `{exc}`; use `error_type=type(exc).__name__` + `error=safe_error_description(exc)`. Never `exc_info=True`. Never `logger.exception(...)` (attaches traceback whose frame-locals serialise in-scope `client_secret` / `refresh_token` / Fernet ciphertext); replace with `except ... as exc: logger.error(EVENT, ..., error_type=type(exc).__name__, error=safe_error_description(exc))`. OTel: `span.record_exception(exc)` forbidden; use `span.set_attribute("exception.message", safe_error_description(exc))` + `record_exception=False, set_status_on_exception=False`. Enforced by `check_logger_exception_str_exc.py`.

## API startup lifecycle

- Two phases: **construction** (`create_app` body) wires synchronous services; **on_startup** (`_build_lifecycle.on_startup`) wires services that need a connected persistence backend.
- Construction-phase ordering invariants: `agent_registry` must be built BEFORE `auto_wire_meetings`; `tunnel_provider` is wired unconditionally (not gated by `integrations.enabled`).
- On-startup ordering invariants: `SettingsService` auto-wire must precede `WorkflowExecutionObserver` registration (so it picks up resolver-driven `max_subworkflow_depth` instead of the seed default); `OntologyService` wires after `persistence.connect()` via `_wire_ontology_service`. Cost-dial services (`BudgetConfig`, `CostForecastRepository`, `CostForecaster`, `StubBenchmarkScoreProvider`, `ParetoAnalyzer`) wire via `_try_wire_cost_dial` AFTER persistence connects; it is best-effort (logs `BUDGET_FORECAST_UNAVAILABLE` and the controllers 503 if it fails or persistence is absent) and idempotent (skips when already wired), so a transient shared-app boot does not poison startup. The approved forecast's `forecast_id` + `ceiling_amount` are stamped onto the `Task` in the work pipeline's intake phase (`WorkPipelineService._link_forecast`) so the in-loop `BudgetChecker` enforces the per-brief ceiling and the engine can stamp halt context for the resume banner. Knowledge substrate wires via `_wire_knowledge_engine` AFTER persistence connects; it is best-effort and gated on `has_persistence` AND `has_memory_backend` (logs `KNOWLEDGE_SUBSTRATE_UNAVAILABLE` and the knowledge controllers + MCP handlers 503 if either is absent), so missing memory backend in dev does not poison startup. `EnvironmentService` (per-project reproducible environments) wires in `_install_runtime_services` behind `has_persistence` and is threaded into `AgentEngineExecutionService` via `build_runtime_services`; the worker provisions ambiently (`ActiveSandboxEnvironment` contextvar) before the engine run, so a missing workspace logs `ENVIRONMENT_PROVISION_SKIPPED` rather than silently dropping the declared env. Mid-flight steering splits its wiring in two by dependency: the steering INBOX (read path) is built from `persistence.project_brain` and injected into the boot `AgentEngine` in the runtime-services step (persistence-only, memory-independent `list_current` projection), while the steering SERVICE (write path) wires in `_wire_steering_service` AFTER `_wire_project_brain` (memory-gated brain) via partial `app_state.wire(CockpitStateSlice, ...)` (NOT `swap_slice`, so the construction-phase `steering_notifier` and the later `steering_service` coexist on the slice). Wiring the service inside `_wire_cockpit_services` would race the brain and 503 forever. The red-team report repo is published on `SecurityStateSlice.red_team_reports` during `_install_runtime_services` (decoupled from the review gate, via partial `app_state.wire`), and `_wire_deliverable_receipts` reads it so a receipt's `red_team` section degrades to empty rather than erroring when the subsystem is off.
- Runtime services: `synthorg.workers.runtime_builder.build_runtime_services` selects behind ONE provider-present switch and returns a `RuntimeServices` pair (worker execution service + multi-agent coordinator) built from a SINGLE shared boot `AgentEngine`: `AgentEngineExecutionService` + a `build_coordinator(...)` coordinator with a provider, `NoProviderExecutionService` + `None` coordinator as the empty-company backstop. The `_install_runtime_services` boot hook installs both via the `AppState.worker_execution_service` and `AppState.coordinator` seams; it is appended FIRST after the persistence/SettingsService hooks so the once-only `set_worker_execution_service` and if-absent `set_coordinator_if_absent` seams cannot lose the race with the worker property's lazy `LifecycleAdvancingExecutionService` default. Empty-company rejects task creation at the controller (`AgentRuntimeNotConfiguredError`, 4014) and `/coordinate` honestly 503s (no coordinator). `swap_worker_execution_service` / `swap_coordinator` / `swap_provider_registry` hold a lock (synchronised against lazy reads).
- Setup completion: `post_setup_reinit()` (provider reload, agent bootstrap, AND runtime-services rebuild + dual hot-swap of the worker execution service and coordinator, defined in `src/synthorg/api/controllers/setup/agent_helpers.py`) propagates failures, and `settings_svc.set("api", "setup_complete", "true")` only runs if reinit returns clean. The whole check/validate/reinit/persist sequence is serialised under `COMPLETE_LOCK` in the same module so two concurrent `/setup/complete` requests cannot race on the flag write. A half-configured runtime presenting itself as "complete" is worse than a clear error the operator can retry after fixing the underlying provider config.

## MCP / Telemetry / Resilience

- **MCP**: 200+ tools across 21 domain modules under `meta/mcp/domains/`. Define `ToolHandler` + `args_model`; call `require_admin_guardrails()` on admin tools; route through service layers. See [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md).
- **Telemetry**: opt-in, off by default. Every event property must be in `_ALLOWED_PROPERTIES`. See [telemetry.md](docs/reference/telemetry.md).
- **Resilience**: provider calls go through `BaseCompletionProvider` (retry + rate limit); never implement retry in driver subclasses. Retryable: `RateLimitError`, `Provider{Timeout,Connection,Internal}Error`. WebSocket: per-frame timeout closes silent peers (1008); revalidation saturation closes (4011). Non-provider transient I/O (e.g. git push/fetch) uses `core.resilience.GeneralRetryHandler` with a `retryable` predicate, never a hand-rolled loop; see [retry-patterns.md](docs/reference/retry-patterns.md).
- **Conversational org interface (EPIC #1967)**: four opt-in (default-off) modes on `/meta/chat/*`, each built by an ENFORCED ghost-wiring factory and 503-ing when a dependency is absent. **propose** (`build_chief_of_staff_proposer`, `propose_enabled`): clarify-or-park `WorkItem`s; 503s without provider / connected persistence (the work pipeline is needed only at approval-decision time, Flow 0, not at endpoint build). **routing** (`build_role_router`, `routing_enabled`): per-turn concern routing to the most-senior role agent (`llm` or `keyword` strategy). **group chat** (`build_group_chat_service`, `group_chat_enabled`) + **agent invite** (`GroupInviteCoordinator`, `invite_enabled` + wired approval store). **direct MCP acting** (`build_conversational_actor`, `direct_mcp_enabled`): **FAIL-CLOSED**, the builder returns `None` (endpoint 503s) when `has_security_governance` is False, since without governance the SecOps escalate-and-park step is absent and permitted write/admin actions would run ungated. Approval decisions route through `signal_resume_intent`: Flow 0 `CONVERSATIONAL_INTAKE` -> Flow 0.5 `CONVERSATIONAL_INVITE` -> parked-context (direct acting) -> review-gate. `ConversationLockRegistry.hold` serialises per-conversation turns (self-evicting). Human content wrapped via `wrap_untrusted(TAG_TASK_DATA, ...)` (SEC-1).

## Testing (detail in [conventions.md](docs/reference/conventions.md))

- Markers: `@pytest.mark.{unit,integration,e2e,slow}`. Async `auto`. Timeout 30s global. Coverage 80% min.
- xdist `-n 8 --dist=loadfile` auto-applied via pyproject `addopts` (`loadfile` prevents 3.14+Windows ProactorEventLoop leak).
- Windows: unit tests pin pytest-asyncio loops to `SelectorEventLoop` via the per-conftest `pytest_asyncio_loop_factories` hook in `tests/unit/conftest.py` (avoids the 3.14 IOCP teardown race). Subprocess-driving tiers (`tests/unit/tools/`, `tests/unit/engine/workspace/git_backend/`) shadow the hook with `ProactorEventLoop`; pluggy's reverse-order invocation under `firstresult=True` lets the deeper conftest's hook win.
- Test doubles: ladder in [conventions.md](docs/reference/conventions.md) section 12.1. `FakeClock` for the Clock seam, `mock_of[T](**overrides)` for typed-boundary substitutions, `SimpleNamespace` for attribute-bags. Bare `MagicMock` at a typed boundary (constructor / fn arg / annotated local / typed fixture return) is blocked by `scripts/check_mock_spec.py` (zero-tolerance, no baseline).
- FakeClock and `mock_of` import from `tests._shared`; inject via `clock=` and the helper's spec subscript.
- Boundary `@suppress_type_checks` lives at `tests/unit/api/conftest.py` on `api.app.create_app`: source-side import cycles defeat typeguard's eager signature inspection on this entry point; wrapping at the test side keeps `typeguard` a pure test dep.
- API test client: HTTP tests use the `async_test_client` fixture (`LoopAsyncClient` from `tests._shared`, portal-free: drives ASGI lifespan + requests on the test's own loop, no `BlockingPortal`); websocket tests use the sync `ws_test_client` (litestar `TestClient`, whose `WebSocketTestSession` is sync/portal-backed). The Windows `socket.socketpair` retry wrapper in `tests/conftest.py` is a PERMANENT guard for unfixed CPython 122797 (per-test loop self-pipe creation), not removable by the async-client migration.
- Vendor-agnostic: NEVER use real vendor names in project code/tests. Use `example-provider`, `test-provider`, `example-{large,medium,small}-001`. Allowed in `.claude/`, third-party imports, `providers/presets.py`, `web/public/provider-logos/`.
- Hypothesis: 10 deterministic CI examples; failures are real bugs (fix + add `@example(...)`).
- Flaky: NEVER skip/xfail; fix fundamentally. Use `asyncio.Event().wait()` not `sleep(large)`.
- Dual-backend conformance: `tests/conformance/persistence/` consumes `backend` fixture (SQLite + Postgres). Enforced by `check_dual_backend_test_parity.py`.
- Postgres fixture (local): defaults to a testcontainers-managed `postgres:18-alpine`. Bypass testcontainers and use a local Postgres (e.g. `docker compose up postgres`) by exporting `SYNTHORG_TEST_POSTGRES_HOST` / `PORT` / `USER` / `PASSWORD` / `DB` before running pytest. The `postgres_container` fixture in `tests/{conformance,integration}/persistence/conftest.py` detects them and yields a connection-info proxy without invoking testcontainers; CI uses the same env vars to point at a `services: postgres` service container.

## Git

- Commits: `<type>: <description>` (feat/fix/refactor/docs/test/chore/perf/ci); commitizen-enforced.
- Signed commits required on protected refs (GPG/SSH or GitHub App via `synthorg-repo-bot`).
- Branches: `<type>/<slug>` from main.
- Pre-commit/pre-push hooks: `.pre-commit-config.yaml`. Tool-call gates: `.claude/settings.json` PreToolUse (`scripts/check_*.sh`/`.py`).
- Squash merge. PR body becomes squash commit; trailers (`Release-As`, `Closes #N`) must be in PR body.
- GitHub queries: `gh issue list` via Bash, NOT MCP `list_issues`.

## Workflow

- After every squash merge → `/post-merge-cleanup`.
- CLI is Docker-only (init/start/stop/status); features go in dashboard + REST API.
- **Subagent models (cost safety)**: every agent definition in `.claude/agents/` MUST pin an explicit `model:` (never omit, never `model: inherit`). An unpinned/`inherit` agent resolves to the caller's session model (potentially the most expensive tier), so a fan-out of unpinned agents silently runs all of them on it. Likewise pass an explicit `model` to every `Agent` spawn and Workflow `agent()` call / `meta.phases[].model`: `haiku` for mechanical checks, `sonnet` for review/analysis, `opus` only for the heaviest reasoning.
