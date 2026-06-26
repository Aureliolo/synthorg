# CLAUDE.md: SynthOrg

Framework for synthetic organisations (autonomous AI agents orchestrated as a virtual company). Python 3.14+. BUSL-1.1 to Apache 2.0 after the Change Date. Layout: `src/synthorg/` (src layout), `tests/`, `web/` (React 19), `cli/` (Go binary), `evals/` (golden-company benchmark, out-of-package).

Web: see `web/CLAUDE.md`. CLI: see `cli/CLAUDE.md` (`go -C cli`, never `cd cli`). Shell / `cd` / file-write rules: see `~/.claude/rules/common/bash.md`.

## MANDATORY rules

Each rule's full rationale, exemptions, and gate internals live in the linked reference; the line here is the contract.

- **Design Spec (MANDATORY)**: read the `docs/design/` page before implementing; deviations need approval. See [DESIGN_SPEC.md](docs/DESIGN_SPEC.md).
- **Planning (MANDATORY)**: present every plan for accept/deny before coding.
- **Web Dashboard Design System (MANDATORY)**: reuse `web/src/components/ui/`; design tokens only. See `web/CLAUDE.md`.
- **Frontend Is A Pure API Consumer (MANDATORY)**: the dashboard persists NO app/domain state client-side (no `localStorage`/`sessionStorage`/IndexedDB, no `zustand` `persist` as a source of truth). Backend is the sole source: hydrate via GET, write every change through the API, every feature usable over the API alone. Client storage only for transient non-domain UX (auth cookie shim, CSRF token). Gate `check_no_client_state_persistence.py`. See `web/CLAUDE.md`.
- **Regional Defaults (MANDATORY)**: no region/currency/locale privileged; metric units; British English. See [regional-defaults.md](docs/reference/regional-defaults.md).
- **Persistence Boundary (MANDATORY)**: only `src/synthorg/persistence/` may import sqlite/psycopg or emit raw SQL; new repository protocols inherit a generic category from `persistence/_generics.py`; bespoke methods only under ADR-0001 D7; protocols stay `@runtime_checkable`. See [persistence-boundary.md](docs/reference/persistence-boundary.md).
- **Convention Rollout (MANDATORY)**: every convention PR ships its enforcement gate. See [convention-gates.md](docs/reference/convention-gates.md).
- **License Compatibility (MANDATORY)**: no AGPL/GPL (non-LGPL) dependency ships; LGPL deps (`psycopg*`) attributed in `NOTICE`; `golangci-lint` stays an external binary; `pymupdf`/`fitz`/`pymupdf4llm` excluded. Enforced by `check_license_compat.py`. See [license-policy.md](docs/reference/license-policy.md).
- **Configuration Precedence (MANDATORY)**: DB > env > code default (Cat-1) or env > code default (Cat-2); Cat-3 bootstrap secrets are pure env. No `os.environ.get` outside startup (single-key reads only in the bootstrap / entry-point / dynamic-secret / Cat-3 allowlist or behind `# lint-allow: env-read`). Enforced by `check_no_os_environ_outside_bootstrap.py`. See [configuration-precedence.md](docs/reference/configuration-precedence.md).
- **No Hardcoded Values (MANDATORY)**: numerics live in `settings/definitions/`; allowlist 0/1/-1, HTTP codes, hex masks, powers-of-2, and module-level annotated `NAME: Final[...] = literal`. Enforced by `check_no_magic_numbers.py`.
- **Error-Code Uniqueness (MANDATORY)**: each `ErrorCode` maps to exactly one `DomainError` subclass; exemptions are inheritance aliases + the `SHAREABLE_CODES` fallbacks; opt out per-line with `# lint-allow: error-code-uniqueness`. Enforced by `check_error_code_uniqueness.py`. See [errors.md](docs/reference/errors.md).
- **Doc Numeric Claims (MANDATORY)**: in the fixed set of public docs `check_doc_numeric_macros.py` scans (README + the listed `docs/` pages), a digit literal next to a stat noun (tests/providers/agents/tools/domains/namespaces/...) or stat keyword must use `<!--RS:NAME-->` markers from `data/runtime_stats.yaml` (or a per-line `lint-allow: doc-numeric-macros -- <reason>`). See `data/README.md`.
- **Test Regression (MANDATORY)**: timeout/slow failures are source regressions; never edit `tests/baselines/unit_timing.json` or any `scripts/*_baseline.*` (PreToolUse-blocked). The `ALLOW_BASELINE_GROWTH=1` gate-baseline bypass needs explicit approval.
- **Post-Implementation + Pre-PR Review (MANDATORY)**: after an issue, branch + commit + push (no auto-PR; `gh pr create` is blocked); use `/pre-pr-review`, then `/aurelio-review-pr` after the PR. Fix everything valid; no deferring.
- **Module-Size Budget (MANDATORY)**: tiered LOC caps per `# module-kind:` header (controller 400, service/orchestrator 600, complex_service 1100, repository 500, adapter/integration 700, feature 100, code 500 default, tests 800; declarative/generated exempt). Enforced by `check_module_size_budget.py` + `check_no_growth_in_god_modules.py`. See [ADR-0006](docs/decisions/0006-tiered-module-size-policy.md).
- **Import Layering + Architecture Drift (MANDATORY)**: declarative `.importlinter` contracts (forbidden-only, no total-order layers) via `lint-imports`, plus 3 AST gates (raw-SQL boundary, DTO-leak, dependency-inversion). Graph smells gated by `check_architecture_drift.py` vs `data/architecture_report.json`; cold-import cycles by `tests/unit/test_cold_import.py` (keep hub `__init__` light; shared types in `core.*`/`execution.*` leaves). See [import-layering.md](docs/reference/import-layering.md).

## Quick Commands

```bash
uv sync                                             # all deps
uv sync --group docs                                # docs toolchain (zensical + D2)
bash scripts/install_cli_tools.sh                   # one-time per-machine: golangci-lint + lychee + vale
uv run ruff check . --fix                           # lint + auto-fix (whole tree)
uv run ruff format .                                 # format (whole tree)
uv run mypy --num-workers=4 src/ tests/ evals/ docker/ d2_fence.py             # strict type-check
MYPYPATH=. uv run mypy --num-workers=4 --explicit-package-bases scripts/       # scripts/ (flat-dir name clash)
uv run python -m pytest tests/ -m unit                                              # -n 8 --dist=loadfile via pyproject addopts
uv run python -m pytest tests/ -m integration
uv run python -m pytest tests/ -m e2e
uv run python -m pytest tests/ --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80
uv run python -m pytest tests/benchmarks/ --codspeed -n0
uv run python -m evals --help                       # golden-company benchmark CLI (or `make benchmark`)
HYPOTHESIS_PROFILE=dev uv run python -m pytest tests/ -m unit -k properties
HYPOTHESIS_PROFILE=fuzz uv run python -m pytest tests/ -m unit --timeout=0
bash scripts/install_git_hooks.sh                   # one-time per clone: wire core.hooksPath -> scripts/git-hooks
uv run pre-commit run --all-files
uv run pre-commit run lychee --hook-stage pre-push --all-files                      # local Markdown link-check (offline)
vale README.md CLAUDE.md cli/CLAUDE.md web/CLAUDE.md docs/                          # prose linter (Google style + British vocab)
uv run python scripts/check_schema_drift_revisions.py --backend sqlite  # or --backend postgres
PYTHONPATH=. uv run zensical build                  # docs
```

## Reference (load on demand)

- [api-startup-lifecycle.md](docs/reference/api-startup-lifecycle.md): two-phase boot, gated best-effort wiring hooks, ordering invariants.
- [claude-reference.md](docs/reference/claude-reference.md): doc layout, Docker, releasing, CI, dependencies, Hypothesis deep-dive.
- [conventions.md](docs/reference/conventions.md): repository CRUD + file structure, lifecycle naming, response wrapping, validators, event imports, domain errors, frozen ConfigDict, args models, Pydantic v2, async, Clock seam, observability event inventory, MCP handler logging, slice accessors + `_wire_*`/`_try_wire_*`/`set_slice`/`swap_slice`/`wire` semantics.
- [convention-gates.md](docs/reference/convention-gates.md): full gate inventory (enforcement gates + meta-gate + PreToolUse hooks).
- Topic refs: [errors.md](docs/reference/errors.md), [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md), [lifecycle-sync.md](docs/reference/lifecycle-sync.md), [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md), [typed-boundaries.md](docs/reference/typed-boundaries.md), [retry-patterns.md](docs/reference/retry-patterns.md), [scaffolding.md](docs/reference/scaffolding.md), [telemetry.md](docs/reference/telemetry.md), [pluggable-subsystems.md](docs/reference/pluggable-subsystems.md), [protocols-audit.md](docs/reference/protocols-audit.md), [bootstrap-wiring-trace.md](docs/reference/bootstrap-wiring-trace.md).

## Diagrams

`d2` for architecture / nested containers, `mermaid` for flowcharts / sequence / pipelines, Markdown tables for tabular data. D2 theme 200 (Dark Mauve), CLI pinned to v0.7.1 in CI.

## Code conventions (detail in [conventions.md](docs/reference/conventions.md))

- Comments are WHY only; no reviewer citations / issue back-refs / migration framing. Enforced by `check_no_review_origin_in_code.py` + `check_no_migration_framing.py`.
- No `from __future__ import annotations` (3.14 has PEP 649). PEP 758 except: `except A, B:` without parens unless binding.
- Type-only imports stay at module level (so typeguard resolves annotations at runtime); `if TYPE_CHECKING:` only for genuine cycle breakers.
- Type hints on public functions; mypy strict. Google-style docstrings. Line length 88; functions under 50 lines.
- Errors: `<Domain><Condition>Error` from `DomainError`; never inherit `Exception`/`RuntimeError` directly. Enforced by `check_domain_error_hierarchy.py`.
- Pydantic v2: frozen + `extra="forbid"` + `allow_inf_nan=False` on every frozen model (gate `check_frozen_model_extra_forbid.py`; per-line `# lint-allow: frozen-extra-forbid -- <reason>` / `frozen-allow-inf-nan -- <reason>`); `@computed_field` for derived; `UUID` (`default_factory=uuid4`) for entity PK `.id`; `NotBlankStr` for names + string FK fields.
- Args models at every system boundary; `parse_typed()` for every external dict ingestion. Enforced by `check_boundary_typed.py`.
- Immutability: `model_copy(update=...)` or `copy.deepcopy()`; deepcopy at system boundaries.
- Async: `asyncio.TaskGroup` for fan-out/fan-in; helpers catch `Exception` (re-raise `MemoryError`/`RecursionError`).
- Clock seam: `clock: Clock | None = None`; tests inject `FakeClock`. Services own `_lifecycle_lock`; timed-out stops mark unrestartable.
- Untrusted content (SEC-1): `wrap_untrusted()` from `engine.prompt_safety`; `HTMLParseGuard` for HTML.
- Repository CRUD: `save(entity)`, `get(id)`, `delete(id) -> bool`, `list_items(...)`, `query(...)` returning tuples.
- Datetime in persistence: `parse_iso_utc` / `format_iso_utc` from `persistence._shared` (reject naive); `normalize_utc` for already-typed.

## Logging (detail in [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md))

- `from synthorg.observability import get_logger`; variable always `logger`. Never `import logging` / `print()` in app code.
- Event names from `observability.events.<domain>` constants; structured kwargs (`logger.info(EVENT, key=value)`).
- Error paths log WARNING/ERROR with context before raising; state transitions log INFO via `*_STATUS_TRANSITIONED` AFTER the persistence write.
- **Secret-log redaction (SEC-1)**: never `error=str(exc)` or interpolate `{exc}`; use `error_type=type(exc).__name__` + `error=safe_error_description(exc)`. Never `exc_info=True` or `logger.exception(...)` (frame-locals serialise secrets), enforced by `check_logger_exception_str_exc.py`; never OTel `span.record_exception(exc)`, enforced by `check_otlp_span_redaction.py`.

## API startup, MCP, Resilience

- **Startup lifecycle**: two-phase boot (construction wires synchronous services; on-startup wires persistence-dependent ones), with gated best-effort wiring hooks and load-bearing ordering invariants. Full trace in [api-startup-lifecycle.md](docs/reference/api-startup-lifecycle.md).
- **MCP**: 245 tools across 22 domain modules under `meta/mcp/domains/`. Define `ToolHandler` + `args_model`; call `require_admin_guardrails()` on admin tools; route through service layers. See [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md).
- **Telemetry**: opt-in, off by default. Every event property must be in `_ALLOWED_PROPERTIES`. See [telemetry.md](docs/reference/telemetry.md).
- **Resilience**: provider calls go through `BaseCompletionProvider` (retry + rate limit); never retry in driver subclasses. `providers.retry_max_attempts` (Cat-1) is the default `max_retries` only when a provider left its own at the `RetryConfig` default. Non-provider transient I/O uses `core.resilience.GeneralRetryHandler`, never a hand-rolled loop (carve-outs in [retry-patterns.md](docs/reference/retry-patterns.md)).
- **Conversational org interface**: modes on `/meta/chat/*`, each built by an enforced ghost-wiring factory that 503s when a dependency is absent. The four conversational capabilities (explain-chat / propose / routing / group-chat) default ON and are live-gated per request via `ensure_feature_enabled` (`api/_feature_gate.py`) so `chief_of_staff.*_enabled` toggles take effect next request with no restart; agent-invite + direct-MCP acting stay OFF by default, and direct MCP acting is FAIL-CLOSED without security governance. Human content wrapped via `wrap_untrusted(TAG_TASK_DATA, ...)` (SEC-1).

## Testing (detail in [conventions.md](docs/reference/conventions.md))

- Markers: `@pytest.mark.{unit,integration,e2e,slow}`. Async `auto`. Timeout 30s global. Coverage 80% min.
- xdist `-n 8 --dist=loadfile` auto-applied via pyproject `addopts` (`loadfile` prevents the 3.14+Windows ProactorEventLoop leak).
- Windows: unit tests pin pytest-asyncio loops to `SelectorEventLoop` (per-conftest `pytest_asyncio_loop_factories` in `tests/unit/conftest.py`); subprocess-driving tiers shadow it with `ProactorEventLoop`.
- Test doubles (ladder in [test-doubles.md](docs/reference/test-doubles.md)): `FakeClock` for the Clock seam, `mock_of[T](**overrides)` for typed-boundary substitutions, `SimpleNamespace` for attribute-bags. Bare `MagicMock` at a typed boundary is blocked by `check_mock_spec.py`. Import from `tests._shared`.
- Entity ids from `tests._shared`: `as_uuid(label)` for `UUID` PK, `sid(label)` for string FK / wire form, `coerce_id` / `as_pk` to normalise; never bare `uuid4()` for a cross-referenced id.
- API test client: HTTP tests use `async_test_client` (`LoopAsyncClient`, portal-free); websocket tests use the sync `ws_test_client`. The Windows `socket.socketpair` retry wrapper in `tests/conftest.py` is a permanent guard for CPython 122797.
- Vendor-agnostic: NEVER use real vendor names in project code/tests. Use `example-provider`, `test-provider`, `example-{large,medium,small}-001`. Allowed in `.claude/`, third-party imports, `providers/presets.py`, `web/public/provider-logos/`.
- Hypothesis: 10 deterministic CI examples; failures are real bugs (fix + add `@example(...)`). Flaky: NEVER skip/xfail; fix fundamentally (`asyncio.Event().wait()`, not `sleep`).
- Dual-backend conformance: `tests/conformance/persistence/` consumes the `backend` fixture (SQLite + Postgres); enforced by `check_dual_backend_test_parity.py`. Local Postgres: export `SYNTHORG_TEST_POSTGRES_*` to bypass testcontainers.

## Git

- Commits: `<type>: <description>` (feat/fix/refactor/docs/test/chore/perf/ci); commitizen-enforced. Branches `<type>/<slug>` from main. Squash merge; trailers (`Release-As`, `Closes #N`) go in the PR body.
- Signed commits required on protected refs.
- Pre-commit/pre-push hooks in `.pre-commit-config.yaml`; tool-call gates in `.claude/settings.json` PreToolUse.
- A failed pre-push leaves a `<hook>-FAILED` marker under `synthorg-hooks/` that blocks the next push; after fixing the root cause, clear it with `bash scripts/clear_prepush_marker.sh` (never a raw `rm`).
- GitHub queries: `gh issue list` via Bash, NOT MCP `list_issues`.

## Workflow

- CLI is Docker-only (init/start/stop/status); features go in the dashboard + REST API.
- **Subagent models (cost safety)**: pin an explicit `model:` in every `.claude/agents/` definition (never omit / `inherit`), and pass an explicit `model` to every `Agent` spawn and Workflow `agent()` / `meta.phases[].model`: `haiku` for mechanical checks, `sonnet` for review/analysis, `opus` only for the heaviest reasoning.
