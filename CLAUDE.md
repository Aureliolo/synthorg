# CLAUDE.md: SynthOrg

Framework for synthetic organisations (autonomous AI agents orchestrated as a virtual company). Python 3.14+. BUSL-1.1 → Apache 2.0 after Change Date. Layout: `src/synthorg/` (src layout), `tests/`, `web/` (React 19), `cli/` (Go binary).

Web: see `web/CLAUDE.md`. CLI: see `cli/CLAUDE.md` (use `go -C cli`, never `cd cli`). Shell: see `~/.claude/rules/common/bash.md` (canonical for `cd` / `git -C` / Bash file-write rules).

## MANDATORY one-liners

- **Design Spec (MANDATORY)**: read `docs/design/` page before implementing; deviations need approval. See [DESIGN_SPEC.md](docs/DESIGN_SPEC.md).
- **Planning (MANDATORY)**: present every plan for accept/deny before coding.
- **Web Dashboard Design System (MANDATORY)**: reuse `web/src/components/ui/`; design tokens only. Detail in `web/CLAUDE.md`.
- **Regional Defaults (MANDATORY)**: no region/currency/locale privileged; metric units; British English. See [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md).
- **Persistence Boundary (MANDATORY)**: only `src/synthorg/persistence/` may import sqlite/psycopg or emit raw SQL. See [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md).
- **Convention Rollout (MANDATORY)**: every convention PR ships its enforcement gate. See [docs/reference/convention-gates.md](docs/reference/convention-gates.md).
- **Configuration Precedence (MANDATORY)**: DB > env > code default via `SettingsService`/`ConfigResolver` (Cat-1) or env > code default (Cat-2, `read_only_post_init`); Cat-3 bootstrap secrets are pure env at the boot site. YAML is a company-template ingestion format, not a precedence tier. No `os.environ.get` outside startup; pre-init Cat-2 reads use `settings.bootstrap_resolver.resolve_init_value`. See [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md).
- **No Hardcoded Values (MANDATORY)**: numerics live in `settings/definitions/`; allowlist 0/1/-1, HTTP codes, hex masks, powers-of-2, and module-level annotated named constants of the form `NAME: int|float|Final|Final[int]|Final[float] = literal`. Enforced by `scripts/check_no_magic_numbers.py`.
- **Doc Numeric Claims (MANDATORY)**: numerics in README + public docs sourced from `data/runtime_stats.yaml` via `<!--RS:NAME-->` markers. See `data/README.md`.
- **Test Regression (MANDATORY)**: timeout/slow failures = source-code regression; never edit `tests/baselines/unit_timing.json` or any `scripts/*_baseline.{txt,json}` / `scripts/_*_baseline.py`. Both families are PreToolUse-blocked. Per-invocation bypass for gate baselines: `ALLOW_BASELINE_GROWTH=1 git commit ...` (requires explicit user approval).
- **Post-Implementation + Pre-PR Review (MANDATORY)**: after issue: branch + commit + push (no auto-PR); use `/pre-pr-review` (gh pr create is hookify-blocked). After PR: `/aurelio-review-pr` for external feedback. Fix EVERYTHING valid; no deferring.

## Quick Commands

```bash
uv sync                                             # all deps
uv sync --group docs                                # docs toolchain (zensical + D2)
bash scripts/install_cli_tools.sh                   # one-time per-machine: d2 + golangci-lint (CI installs separately)
uv run ruff check src/ tests/ --fix                 # lint + auto-fix
uv run ruff format src/ tests/                      # format
uv run mypy src/ tests/                             # strict type-check
uv run python -m pytest tests/ -m unit                                              # -n 8 --dist=loadfile via pyproject addopts
uv run python -m pytest tests/ -m integration
uv run python -m pytest tests/ -m e2e
uv run python -m pytest tests/ --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80
uv run python -m pytest tests/benchmarks/ --codspeed -n0
HYPOTHESIS_PROFILE=dev uv run python -m pytest tests/ -m unit -k properties
HYPOTHESIS_PROFILE=fuzz uv run python -m pytest tests/ -m unit --timeout=0
uv run pre-commit run --all-files
uv run python scripts/check_schema_drift_revisions.py --backend sqlite  # or --backend postgres
PYTHONPATH=. uv run zensical build                  # docs
```

## Reference (load on demand)

- [docs/reference/claude-reference.md](docs/reference/claude-reference.md): Doc layout, Docker, releasing, CI, dependencies, Hypothesis deep-dive
- [docs/reference/conventions.md](docs/reference/conventions.md): repository CRUD, lifecycle, response wrapping, validators, event imports, domain errors, file structure, frozen ConfigDict, args models, Pydantic v2, async, Clock seam, observability event-name inventory, repository CRUD method names, MCP handler logging centralisation, repository file structure, registering MANDATORY rules, `activate_*` / `deactivate_*` lifecycle naming
- [docs/reference/convention-gates.md](docs/reference/convention-gates.md): gate inventory (<!--RS:convention_gates--> enforcement gates + meta-gate + PreToolUse hooks)
- [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md), [persistence-boundary.md](docs/reference/persistence-boundary.md), [configuration-precedence.md](docs/reference/configuration-precedence.md), [errors.md](docs/reference/errors.md), [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md), [lifecycle-sync.md](docs/reference/lifecycle-sync.md), [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md), [typed-boundaries.md](docs/reference/typed-boundaries.md), [retry-patterns.md](docs/reference/retry-patterns.md), [scaffolding.md](docs/reference/scaffolding.md), [audit-category-gate-coverage.md](docs/reference/audit-category-gate-coverage.md), [dead-api-endpoints.md](docs/reference/dead-api-endpoints.md), [pluggable-subsystems.md](docs/reference/pluggable-subsystems.md), [protocols-audit.md](docs/reference/protocols-audit.md), [telemetry.md](docs/reference/telemetry.md)

## Diagrams

`d2` for architecture / nested containers, `mermaid` for flowcharts / sequence / pipelines. Markdown tables for tabular data. D2 theme 200 (Dark Mauve), D2 CLI pinned to v0.7.1 in CI.

## Code conventions (detail in [conventions.md](docs/reference/conventions.md))

- Comments WHY only; no reviewer citations / issue back-refs / migration framing. Enforced by `check_no_review_origin_in_code.py` + `check_no_migration_framing.py`.
- No `from __future__ import annotations` (3.14 has PEP 649). PEP 758 except: `except A, B:` no parens unless binding.
- Type hints on public functions; mypy strict. Google-style docstrings. Line length 88; functions <50 lines; files <800 lines.
- Errors: `<Domain><Condition>Error` from `DomainError`; never inherit `Exception`/`RuntimeError`/etc directly. Enforced by `check_domain_error_hierarchy.py`.
- Pydantic v2 frozen + `extra="forbid"` on API DTOs (Request/Response/Snapshot/Result/Envelope/Status/Info/Summary suffixes); `@computed_field` for derived; `NotBlankStr` for identifiers.
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
- **Secret-log redaction (SEC-1)**: never `error=str(exc)` or interpolate `{exc}`; use `error_type=type(exc).__name__` + `error=safe_error_description(exc)`. Never `exc_info=True`. OTel: `span.record_exception(exc)` forbidden; use `span.set_attribute("exception.message", safe_error_description(exc))` + `record_exception=False, set_status_on_exception=False`. Enforced by `check_logger_exception_str_exc.py`.

## API startup lifecycle

- Two phases: **construction** (`create_app` body) wires synchronous services; **on_startup** (`_build_lifecycle.on_startup`) wires services that need a connected persistence backend.
- Construction-phase ordering invariants: `agent_registry` must be built BEFORE `auto_wire_meetings`; `tunnel_provider` is wired unconditionally (not gated by `integrations.enabled`).
- On-startup ordering invariants: `SettingsService` auto-wire must precede `WorkflowExecutionObserver` registration (so it picks up resolver-driven `max_subworkflow_depth` instead of the seed default); `OntologyService` wires after `persistence.connect()` via `_wire_ontology_service`.

## MCP / Telemetry / Resilience

- **MCP**: 200+ tools across 15 domain modules under `meta/mcp/domains/`. Define `ToolHandler` + `args_model`; call `require_admin_guardrails()` on admin tools; route through service layers. See [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md).
- **Telemetry**: opt-in, off by default. Every event property must be in `_ALLOWED_PROPERTIES`. See [telemetry.md](docs/reference/telemetry.md).
- **Resilience**: provider calls go through `BaseCompletionProvider` (retry + rate limit); never implement retry in driver subclasses. Retryable: `RateLimitError`, `Provider{Timeout,Connection,Internal}Error`. WebSocket: per-frame timeout closes silent peers (1008); revalidation saturation closes (4011).

## Testing (detail in [conventions.md](docs/reference/conventions.md))

- Markers: `@pytest.mark.{unit,integration,e2e,slow}`. Async `auto`. Timeout 30s global. Coverage 80% min.
- xdist `-n 8 --dist=loadfile` auto-applied via pyproject `addopts` (`loadfile` prevents 3.14+Windows ProactorEventLoop leak).
- Windows: unit tests use `WindowsSelectorEventLoopPolicy` (3.14 IOCP teardown race). Subprocess tests override back.
- Test doubles: ladder in [conventions.md](docs/reference/conventions.md) section 12.1. `FakeClock` for the Clock seam, `mock_of[T](**overrides)` for typed-boundary substitutions, `SimpleNamespace` for attribute-bags. Bare `MagicMock` at a typed boundary (constructor / fn arg / annotated local / typed fixture return) is blocked by `scripts/check_mock_spec.py` (zero-tolerance, no baseline).
- FakeClock and `mock_of` import from `tests._shared`; inject via `clock=` and the helper's spec subscript.
- Vendor-agnostic: NEVER use real vendor names in project code/tests. Use `example-provider`, `test-provider`, `example-{large,medium,small}-001`. Allowed in `.claude/`, third-party imports, `providers/presets.py`, `web/public/provider-logos/`.
- Hypothesis: 10 deterministic CI examples; failures are real bugs (fix + add `@example(...)`).
- Flaky: NEVER skip/xfail; fix fundamentally. Use `asyncio.Event().wait()` not `sleep(large)`.
- Dual-backend conformance: `tests/conformance/persistence/` consumes `backend` fixture (SQLite + Postgres). Enforced by `check_dual_backend_test_parity.py`.

## Git

- Commits: `<type>: <description>` (feat/fix/refactor/docs/test/chore/perf/ci); commitizen-enforced.
- Signed commits required on protected refs (GPG/SSH or GitHub App via `synthorg-repo-bot`).
- Branches: `<type>/<slug>` from main.
- Pre-commit/pre-push hooks: `.pre-commit-config.yaml`. Hookify rules: `.claude/hookify.*.md`.
- Squash merge. PR body becomes squash commit; trailers (`Release-As`, `Closes #N`) must be in PR body.
- GitHub queries: `gh issue list` via Bash, NOT MCP `list_issues`.

## Workflow

- After every squash merge → `/post-merge-cleanup`.
- CLI is Docker-only (init/start/stop/status); features go in dashboard + REST API.
