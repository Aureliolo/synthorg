# CLAUDE.md: SynthOrg

## Project

- **What**: Framework for building synthetic organizations (autonomous AI agents orchestrated as a virtual company)
- **Python**: 3.14+ (PEP 649 native lazy annotations)
- **License**: BUSL-1.1 with narrowed Additional Use Grant; converts to Apache 2.0 three years after release
- **Layout**: `src/synthorg/` (src layout), `tests/`, `web/` (React 19 dashboard), `cli/` (Go CLI binary)
- **Design**: [DESIGN_SPEC.md](docs/DESIGN_SPEC.md) (pointer to `docs/design/` pages)

## Design Spec (MANDATORY)

ALWAYS read the relevant `docs/design/` page before implementing or planning. Deviations require explicit user approval; update the design page when an approved deviation lands. Never silently diverge.

## Planning (MANDATORY)

Every implementation plan must be presented to the user for accept/deny before coding. Be critical at every phase; surface improvements as suggestions, not silent changes. Prioritize by dependency order, not priority labels.

## Diagrams in Documentation

Use fenced code blocks with language tags: `d2` for architecture / nested containers, `mermaid` for flowcharts / sequence / pipelines. Use markdown tables for tabular data; never use `text` fences with ASCII box-drawing. D2 uses theme 200 (Dark Mauve), dark-only, configured in `mkdocs.yml`; CI pins D2 CLI to v0.7.1 in `.github/workflows/pages.yml`. `diagram-syntax-validator` runs in `/pre-pr-review` and `/aurelio-review-pr`.

## Quick Commands

```bash
uv sync                                                # all deps
uv sync --group docs                                   # docs toolchain (zensical + D2)
uv run ruff check src/ tests/ --fix                    # lint + auto-fix
uv run ruff format src/ tests/                         # format
uv run mypy src/ tests/                                # strict type-check
uv run python -m pytest tests/ -m unit -n 8            # unit (always -n 8)
uv run python -m pytest tests/ -m integration -n 8     # integration
uv run python -m pytest tests/ -m e2e -n 8             # e2e
uv run python -m pytest tests/ -n 8 --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80  # full + coverage
uv run python -m pytest tests/benchmarks/ --codspeed -n0  # CodSpeed (-n0 required)
HYPOTHESIS_PROFILE=dev uv run python -m pytest tests/ -m unit -n 8 -k properties   # 1000 examples
HYPOTHESIS_PROFILE=fuzz uv run python -m pytest tests/ -m unit -n 8 --timeout=0    # 10k examples
uv run pre-commit run --all-files
atlas migrate diff --env sqlite <name>                 # migration from schema.sql diff (or --env postgres)
PYTHONPATH=. uv run zensical build                     # docs build (or `serve` for local preview); PYTHONPATH=. enables d2_fence.py
```

Web: see `web/CLAUDE.md`. Async-leak ceiling lives in `.github/ci/web-async-leaks.max`; full-suite check is `npm --prefix web run test -- --coverage --detect-async-leaks`.

CLI: see `cli/CLAUDE.md`. Use `go -C cli` (never `cd cli`).

## Reference (load on demand)

- [docs/reference/claude-reference.md](docs/reference/claude-reference.md): Doc layout, Docker, Package Structure, Releasing, CI, Dependencies, Hypothesis deep-dive
- [docs/reference/mcp-handler-contract.md](docs/reference/mcp-handler-contract.md): MCP tool handler protocol + envelope + guardrails
- [docs/reference/telemetry.md](docs/reference/telemetry.md): privacy allowlist, env resolution, Docker enrichment
- [docs/reference/pluggable-subsystems.md](docs/reference/pluggable-subsystems.md): protocol/strategy/factory examples
- [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md): SEC-1 untrusted-content fences, HTML XXE, secret-log redaction
- [docs/reference/lifecycle-sync.md](docs/reference/lifecycle-sync.md): async start/stop lifecycle lock pattern
- [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md): persistence exception categories + service layer rules
- [docs/reference/conventions.md](docs/reference/conventions.md): repository CRUD, lifecycle symmetry, response wrapping, validator default, event imports + inventory, domain errors, file structure, frozen ConfigDict, args models, Pydantic v2, async, Clock seam
- [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md): source matrix + exception registry + migration recipe
- [docs/reference/errors.md](docs/reference/errors.md): RFC 9457 codes + HTTP exception handler registration
- [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md): currency / locale / timezone resolution chain
- [docs/reference/typed-boundaries.md](docs/reference/typed-boundaries.md): per-boundary `parse_typed()` inventory + recipe
- [docs/reference/retry-patterns.md](docs/reference/retry-patterns.md): retry-pattern decision tree (transient I/O, semantic self-correction, contention/sync) and the 5 inline-site map
- [docs/reference/scaffolding.md](docs/reference/scaffolding.md): `synthorg new <kind> <domain>` CLI scaffolder usage + per-kind file inventory + shape contract
- [docs/reference/audit-category-gate-coverage.md](docs/reference/audit-category-gate-coverage.md): audit category resolution paths (standing gate / pre-PR mini-pass / architecture / reviewer-enforced)
- [docs/reference/dead-api-endpoints.md](docs/reference/dead-api-endpoints.md): frontend ↔ backend route parity gate, opt-out marker, baseline mechanics

## Web Dashboard Design System (MANDATORY)

Reuse components from `web/src/components/ui/`. Never hardcode hex colors, font-family, pixel spacing, Motion transitions, or BCP 47 locale strings; use design tokens, `@/lib/motion` presets, helpers in `@/utils/format`. Enforced by `scripts/check_web_design_system.py` (PostToolUse on `web/src/` edits). See `web/CLAUDE.md` for inventory + token rules.

## Regional Defaults (MANDATORY)

No default may privilege a region, currency, or locale. Resolution: user/company → browser/system → neutral fallback. Currency, locale, timezone, date/number formats all flow through `@/utils/format` + `@/utils/locale` (frontend) and `DEFAULT_CURRENCY` from `synthorg.budget.currency` (backend); no `_usd` suffixes; metric units only; International / British English UI default (e.g. `colour`, `behaviour`, `organise`, `centred`, `analyse`). Every cost-bearing Pydantic model carries `currency: CurrencyCode`; mixing raises `MixedCurrencyAggregationError` (HTTP 409, error code `4007`). Aggregations over cost-bearing fields call `assert_currencies_match` (from `synthorg.budget.currency`) before reducing. Enforced by `scripts/check_web_design_system.py`, `scripts/check_backend_regional_defaults.py`, `scripts/check_forbidden_literals.py`, and `scripts/check_currency_aggregation_invariant.py` (unguarded `sum` / `math.fsum` / `statistics.mean` / `statistics.fmean`, including bare-name imports `fsum` / `mean` / `fmean`, over `.cost` / `.amount` / `.total_cost` / `.usd` / `.eur`). Per-line opt-outs: `# lint-allow: regional-defaults` (literals/locales) and `# lint-allow: currency-aggregation -- <reason>` (aggregation invariant). See [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md).

## Persistence Boundary (MANDATORY)

`src/synthorg/persistence/` is the only place that may import `aiosqlite` / `sqlite3` / `psycopg` / `psycopg_pool` or emit raw SQL DDL/DML. Every durable feature defines a Protocol in `persistence/<domain>_protocol.py` + concrete impls under `persistence/{sqlite,postgres}/` exposed on `PersistenceBackend`. Controllers and API endpoints access persistence through domain-scoped service layers (e.g. `ArtifactService`, `WorkflowService`, `MemoryService`); services centralize audit logging; repositories must not log mutations themselves. Adding a migration: read `docs/guides/persistence-migrations.md`; never hand-edit SQL or `atlas.sum`. Per-line opt-out: `# lint-allow: persistence-boundary -- <reason>`. Enforced by `scripts/check_persistence_boundary.py`. See [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md).

## Convention Rollout (MANDATORY)

Any PR that establishes or expands a project-wide convention (error
hierarchies, persistence boundary, mock-spec, regional defaults, typed
boundary, settings-to-startup wiring, secret-log redaction, API-DTO
`extra="forbid"`, no-magic-numbers, no-em-dashes, etc.) MUST include the
AST/script gate that prevents regression. PRs proposing a convention
without enforcement are rejected. The gate's job is to catch the SECOND
occurrence of the category; the audit's job is finding the FIRST.

Existing gate inventory (all under `scripts/`):

- `check_backend_regional_defaults.py`
- `check_boundary_typed.py`
- `check_currency_aggregation_invariant.py`
- `check_dead_api_endpoints.py`
- `check_doc_drift_counts.py`
- `check_domain_error_hierarchy.py`
- `check_dual_backend_test_parity.py`
- `check_forbidden_literals.py`
- `check_list_pagination.py`
- `check_logger_exception_str_exc.py`
- `check_mcp_admin_tool_guardrails.py`
- `check_mock_spec.py`
- `check_doc_numeric_macros.py`
- `check_no_bulk_edit.py`
- `check_no_em_dashes.py`
- `check_no_redundant_timeout.py`
- `check_openapi_liveness.py`
- `check_orphan_fixtures.py`
- `check_persistence_boundary.py`
- `check_provider_complete_chokepoint.py`
- `check_dto_forbid_extra.py`
- `check_schema_drift.py`
- `check_setting_to_startup_trace.py`
- `check_web_design_system.py`

Wire each new gate into `.pre-commit-config.yaml` (pre-commit or
pre-push stage as fits) so it runs locally and in CI; per-line opt-outs
use a stable `# lint-allow: <gate-name> -- <reason>` comment.

The machine-readable inventory of every MANDATORY paragraph in the
canonical doc set lives in `scripts/convention_gate_map.yaml`. The
meta-gate `scripts/check_convention_gate_inventory.py` enforces that
every MANDATORY paragraph has either a registered gate or an explicit
`exempt: { reason }` entry; adding a new MANDATORY without updating the
YAML fails pre-push. See [conventions.md §17](docs/reference/conventions.md)
for the registration procedure.

## Configuration Precedence (MANDATORY)

For every mutable setting: **DB > env (`SYNTHORG_<NS>_<KEY>`) > YAML > code default**, resolved through `SettingsService` / `ConfigResolver`. First cold read emits one INFO `settings.value.resolved`; subsequent reads stay DEBUG. Sanctioned exceptions: init-time only (env-only, no registry entry) and read-only post-init (`read_only_post_init=True`; `set()` raises `SettingReadOnlyError`). Direct `os.environ.get(...)` outside startup is forbidden. Register new settings in `src/synthorg/settings/definitions/<namespace>.py`. Ghost-wired settings (consuming service never instantiated at boot) are flagged by `scripts/check_setting_to_startup_trace.py`; per-setting opt-out via `# lint-allow: bootstrap-wiring -- <reason>`. See [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md).

## No Hardcoded Values (MANDATORY)

Every numeric threshold / weight / limit / timeout / scoring policy in business logic lives in `src/synthorg/settings/definitions/<namespace>.py`, not as a bare numeric literal. Sync hot-path consumers read the resolved value from a frozen Pydantic bridge config (e.g. `EngineBridgeConfig`) populated by `ConfigResolver.get_<ns>_bridge_config()` at startup. Bare module-level `_FOO = 1024` constants and bare numeric defaults (`def f(timeout=30)`) are forbidden. Allowlisted: `0`, `1`, `-1` (sentinel/off-by-one), HTTP status codes 100-599 in `status_code=` defaults, hex bit-masks (`0xff`, `0x80`), powers-of-2 in `buffering=` / `chunk_size=` / `buffer_size=` defaults, anything inside `settings/definitions/`, `persistence/migrations/`, `observability/events/`. Per-line opt-out: `# lint-allow: magic-numbers -- <reason>` (mandatory non-empty justification). Enforced by `scripts/check_no_magic_numbers.py` with site-by-site monotonic-shrink baseline at `scripts/no_magic_numbers_baseline.txt`. See [docs/reference/scoring-hyperparameters.md](docs/reference/scoring-hyperparameters.md) for the inventory of migrated settings + rationale.

## Doc Numeric Claims (MANDATORY)

Numeric claims in `README.md` and the public docs (`docs/index.md`, `docs/roadmap/index.md`, `docs/architecture/decisions.md`) about test count, latest release, Mem0 stars, provider count, and subagent count MUST be sourced from `data/runtime_stats.yaml` via inline HTML-comment markers `<!--RS:NAME-->display value<!--/RS-->`. CI runs the generator (`scripts/generate_runtime_stats.py`) and then the injector (`scripts/inject_runtime_stats.py`) BEFORE `zensical build`, so the rendered HTML always reflects fresh values; the HTML comments themselves are stripped by the markdown renderer. The generator refreshes the YAML from authoritative sources (pytest collect, `gh release list`, `gh api`, `synthorg.providers.presets.list_presets`, `.claude/agents` glob) and falls back to committed values when offline. Static historical counts and illustrative scale numbers may carry a per-line opt-out: `<!-- lint-allow: doc-numeric-macros -- <reason> -->` (reason mandatory). Enforced by `scripts/check_doc_numeric_macros.py` (pre-push). See `data/README.md` for schema and regen commands.

## Shell Usage

- **NEVER use `cd` in Bash commands**: cwd is already project root. Exception: `bash -c "cd <dir> && <cmd>"` is safe (child process). Use this for tools without `-C`, e.g. `bash -c "cd web && npm install"`.
- **NEVER use Bash to write files**: use Write or Edit. Forbidden: `cat >`, `cat << EOF`, `echo >`, `echo >>`, `sed -i`, `python -c "open(...).write(...)"`, `tee`. Read-only piping to stdout is fine.

## Code Conventions

- **Comments explain WHY only**, never origin / review / issue context. Forbidden in source / tests / docstrings / commit bodies: reviewer citations (`pre-PR review #N`, `CodeRabbit at file:line`, `Round-N`); in-code issue back-refs (`(#1682)`, `fixes #N`, `as part of #N`); naked `SEC-1` taxonomy in `src/`; migration framing (`ported from`, `renamed from`); round narrative (`round-2 review surfaced this`); self-evident restatements. Keep: hidden constraints, subtle invariants, upstream-bug workarounds (with stable bug-tracker URL), why a non-obvious choice was made.
- **No `from __future__ import annotations`**: Python 3.14 has PEP 649.
- **PEP 758 except**: `except A, B:` (no parens) when not binding; `as exc` requires parens.
- **Type hints**: all public functions; mypy strict.
- **Docstrings**: Google style on public classes / functions (ruff D rules).
- **Immutability**: never mutate; create new objects via `model_copy(update=...)` or `copy.deepcopy()`. Frozen Pydantic for config/identity; `MappingProxyType` for non-Pydantic registries; deepcopy at system boundaries (tool execution, provider serialization, persistence).
- **Config vs runtime state**: separate frozen config models from mutable-via-copy runtime models; never mix in one model.
- **Pydantic v2**: `ConfigDict(frozen=True, allow_inf_nan=False)` everywhere; `extra="forbid"` on every model that doesn't round-trip through `model_dump()` (every API-boundary DTO with a Request / Response / Snapshot / Result / Envelope / Status / Info / Summary suffix in `src/synthorg/api/` is gate-enforced); `@computed_field` for derived values; `NotBlankStr` from `core.types` for identifier / name fields. See [conventions.md](docs/reference/conventions.md) §10.
- **Args models at every system boundary** (`BaseTool`, MCP tool, A2A RPC, WebSocket event): typed Pydantic args model validated before dispatch. See [conventions.md](docs/reference/conventions.md) §9 + [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md).
- **Typed-boundary helper**: every dict ingestion from an external source (MCP args, JWT decode, WebSocket control, audit-chain payload, A2A JSON-RPC, settings security import) calls `parse_typed()` from `synthorg.api.boundary` with a hardcoded `LiteralString` `boundary` label. Enforced by `scripts/check_boundary_typed.py`. See [typed-boundaries.md](docs/reference/typed-boundaries.md).
- **Async concurrency**: prefer `asyncio.TaskGroup` for fan-out / fan-in; wrap independent task bodies in `async def` helpers that catch `Exception` (re-raise only `MemoryError` / `RecursionError`). See [conventions.md](docs/reference/conventions.md) §11.
- **Clock seam**: classes that read time or sleep take `clock: Clock | None = None` (default `SystemClock()`); tests inject `FakeClock`. See [conventions.md](docs/reference/conventions.md) §12.
- **Lifecycle sync**: async `start()` / `stop()` services own a dedicated `self._lifecycle_lock`; timed-out stops mark the service unrestartable. See [lifecycle-sync.md](docs/reference/lifecycle-sync.md).
- **Untrusted-content fences (SEC-1)**: wrap attacker-controllable strings via `wrap_untrusted()` from `synthorg.engine.prompt_safety`; append `untrusted_content_directive(tags)`.
- **HTML parsing (SEC-1)**: never call `lxml.html.fromstring` on attacker input; use `HTMLParseGuard`. See [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md).
- **Pluggable subsystems**: protocol + strategy + factory + config discriminator with safe defaults. Services (which wrap repositories) are a distinct pattern. See [pluggable-subsystems.md](docs/reference/pluggable-subsystems.md).
- **Sizes**: line length 88 (ruff); functions <50 lines; files <800 lines.
- **Errors**: handle explicitly, never swallow. Domain error families register a base-class entry in `EXCEPTION_HANDLERS` (`src/synthorg/api/exception_handlers.py`). Use `<Domain><Condition>Error` inheriting from `DomainError`; any of `Exception` / `RuntimeError` / `LookupError` / `PermissionError` / `ValueError` / `TypeError` / `KeyError` / `IndexError` / `AttributeError` / `OSError` / `IOError` as a direct base in `src/synthorg/` is forbidden. Enforced by `scripts/check_domain_error_hierarchy.py` (pre-push); per-line opt-out: `# lint-allow: domain-error-hierarchy -- <reason>`. See [errors.md](docs/reference/errors.md) + `src/synthorg/core/domain_errors.py`.
- **Repository CRUD**: `save(entity) -> None` (idempotent), `get(id) -> Entity | None`, `delete(id) -> bool`, `list_items(...) -> tuple[Entity, ...]`, `query(...) -> tuple[Entity, ...]`. Query methods always return tuples. See [conventions.md](docs/reference/conventions.md) §14.
- **Validate** at system boundaries (user input, external APIs, config files).
- **Datetime in persistence**: `parse_iso_utc` / `format_iso_utc` from `synthorg.persistence._shared` (both reject naive); `normalize_utc` for relaxed coercion on already-typed `datetime`.

## Logging

- Every business-logic module: `from synthorg.observability import get_logger` then `logger = get_logger(__name__)`. Variable name always `logger`. Carve-outs documented in module docstring.
- **Never** `import logging` / `logging.getLogger()` / `print()` in application code (carve-out: `observability/{setup,sinks,*_handler}.py` for handler bootstrap).
- **Event names**: import constants from `synthorg.observability.events.<domain>`; never string literals. See [conventions.md](docs/reference/conventions.md) §13.
- **Structured kwargs**: `logger.info(EVENT, key=value)`; never `logger.info("msg %s", val)`.
- **Error paths** log at WARNING or ERROR with context before raising / returning.
- **State transitions** log INFO via `*_STATUS_TRANSITIONED` constants (with `from_status` / `to_status` / domain id) AFTER the persistence write succeeds.
- **DEBUG** for object creation, internal flow, key entry/exit. Pure data models, enums, re-exports skip logging.
- **Secret-log redaction (SEC-1)**: never call any `logger` severity with `error=str(exc)` or `error=f"...{exc}..."` (any conversion: default, `!s`, `!r`, `!a`); use `error_type=type(exc).__name__` and `error=safe_error_description(exc)`. Never pass `exc_info=True` to a logger call -- structlog's exc-info processor serialises traceback frame-locals (in-scope tokens / Fernet ciphertext / connection URIs) to the sink. Per-line opt-out for genuine framework-boundary handlers via `# lint-allow: exc-info -- <reason>` (mandatory non-empty reason) on the same physical line as `exc_info=True,`. Enforced by `scripts/check_logger_exception_str_exc.py`: AST-walks the `error=` subtree (catches wrapped forms via Subscript / BinOp / IfExp / BoolOp / JoinedStr / Dict-unpack); flags FormattedValue interpolations of leaves matching `_EXCEPTION_LEAF_NAMES` (`exc, e, err, error, exception, cause, original, inner, _inner`); detects one-level Name-binding indirection (`error_msg = str(exc); ...; error=error_msg`); skips `Call.args` and class-introspection chains (`type(exc).__name__`, `exc.__class__.__name__`, `safe_error_description(exc)`) so canonical safe shapes do not trip. See [sec-prompt-safety.md](docs/reference/sec-prompt-safety.md).

## MCP Handler Layer

200+ tools across 15 domain modules under `src/synthorg/meta/mcp/domains/`. Implementing a handler: define `ToolHandler` in `src/synthorg/meta/mcp/handlers/<domain>.py`, declare `args_model`, call `require_admin_guardrails(arguments, actor)` on any `admin_tool`, route through service-layer facades (never `app_state.persistence.*` directly), emit the three log paths via `common_logging` helpers. See [mcp-handler-contract.md](docs/reference/mcp-handler-contract.md), `docs/design/tools.md`, `docs/design/observability.md`.

## Telemetry (Product)

Opt-in, off by default. Every event property must be in `_ALLOWED_PROPERTIES` keyed by event type; unknown keys raise `PrivacyViolationError` and are dropped. Never bypass the scrubber. See [telemetry.md](docs/reference/telemetry.md).

## Resilience

- All provider calls go through `BaseCompletionProvider` which applies retry + rate limiting automatically. **Never** implement retry in driver subclasses or calling code.
- `RetryConfig` / `RateLimiterConfig` set per-provider in `ProviderConfig`. Retryable: `RateLimitError`, `ProviderTimeoutError`, `ProviderConnectionError`, `ProviderInternalError`. Non-retryable raise immediately.
- `RetryExhaustedError` triggers fallback chains in the engine layer. Rate limiter respects `RateLimitError.retry_after`.
- WebSocket per-frame timeout (DoS): silent peer closed with code 1008 after `api.ws_frame_timeout_seconds` (default 30s). Revalidation failures tracked via `_SlidingWindowRateLimiter` (`api.ws_revalidation_window_seconds` 60s, `api.ws_revalidation_max_failures` 5); saturation closes the socket with code 4011.

## Test Regression (MANDATORY)

When tests fail due to timeout / slowness / xdist contention: NEVER delete, skip, or `xfail`; NEVER `--no-verify`; NEVER edit `tests/baselines/unit_timing.json` (enforced by `scripts/check_no_edit_baseline.sh`). First run `uv run python -m pytest tests/unit/ -m unit -n 8 --durations=50 --durations-min=0.5 -q --no-header` and compare against the baseline. Suite time exceeding `baseline * 1.3` is a source-code regression; fix the source, not the tests. The `pytest_sessionfinish` hook in `tests/conftest.py` warns loudly; trust it.

## Testing

- **Markers**: `@pytest.mark.unit` / `integration` / `e2e` / `slow`.
- **Mock-spec gate**: every `Mock()` / `AsyncMock()` / `MagicMock()` in `tests/` MUST declare `spec=ConcreteClass`. Pre-existing sites frozen in `scripts/mock_spec_baseline.txt`; regenerate via `uv run python scripts/check_mock_spec.py --update`. Without `spec=` mocks silently absorb every attribute access.
- **Shared mocks**: use `mock_dispatcher` from `tests/conftest.py` (`AsyncMock(spec=NotificationDispatcher)`).
- **Time-driven tests**: import `FakeClock` from `tests._shared.fake_clock`; inject via `clock=` parameter. `FakeClock.sleep` advances virtual time and yields once via `asyncio.sleep(0)`. Patch `time.monotonic()` / `asyncio.sleep()` globals only for legacy paths without a `Clock` seam.
- **Benchmarks**: `tests/benchmarks/` use `@pytest.mark.benchmark`, NOT marked `unit` (skipped by `-m unit`). Run via `--codspeed -n0`. Heap-ceiling tests live under `tests/unit/perf/` with `@pytest.mark.unit`.
- **Coverage**: 80% minimum (CI; benchmarks excluded).
- **Async**: `asyncio_mode = "auto"`; no manual `@pytest.mark.asyncio`.
- **Timeout**: 30s per test (global in `pyproject.toml`); don't add per-file `timeout(30)` markers; non-default like `timeout(60)` is allowed.
- **Parallelism**: `pytest-xdist -n 8 --dist=loadfile` (always). `loadfile` prevents the cumulative resource leak `worksteal` triggers on Python 3.14 + Windows ProactorEventLoop.
- **Event loop on Windows**: unit tests run under `WindowsSelectorEventLoopPolicy` (set by `tests/unit/conftest.py`) to avoid a Python 3.14 IOCP teardown race ([CPython #116773](https://github.com/python/cpython/issues/116773) and family) that crashes xdist workers under repeated event-loop creation. Tool tests that drive real `asyncio.create_subprocess_exec` (git, sandbox) override back to the default policy in `tests/unit/tools/conftest.py`.
- **Isolation regression gate**: `scripts/run_affected_tests.py` re-runs the affected subset under `pytest-repeat --count 2 --max-worker-restart=4` after the green pass and classifies the outcome: real test failures or the same test crashing on multiple iterations block the gate; native worker crashes scattered across unrelated tests are advisory (gate still passes). Opt out via `SYNTHORG_SKIP_ISOLATION_GATE=1`.
- **Logger spying antipattern**: never `monkeypatch.setattr(module.logger, "info", spy)`; the `BoundLoggerLazyProxy` caches the stale bound method via `__dict__`. Use `try/finally del proxy.<level>` instead; see `_logger_info_spy` in `tests/unit/settings/test_service.py`.
- **Parametrize**: prefer `@pytest.mark.parametrize` for similar cases.
- **Dual-backend conformance**: persistence repositories ship parametrised conformance tests under `tests/conformance/persistence/test_<domain>_repository.py` that consume the `backend` fixture from `tests/conformance/persistence/conftest.py`; the fixture runs each test against both SQLite and Postgres. All `test_*` signatures must accept `backend` (no concrete `aiosqlite.Connection` / `psycopg` typing) and must avoid `if backend.backend_name == "..."` body conditionals. Enforced by `scripts/check_dual_backend_test_parity.py`; per-line opt-out `# lint-allow: dual-backend-parity -- <reason>`.
- **Vendor-agnostic everywhere**: NEVER use real vendor names (Anthropic, OpenAI, Claude, GPT, etc.) in project-owned code/tests/comments/docstrings/configs. Use `example-provider`, `example-{large,medium,small}-001`. Allowed in: `.claude/` files, third-party import paths, `src/synthorg/providers/presets.py` (user-facing runtime data), `web/public/provider-logos/*.svg`. Tests use `test-provider`, `test-small-001`.
- **Property-based**: Hypothesis (Python), fast-check (React), `testing.F` (Go). CI runs 10 deterministic examples (`derandomize=True`). Hypothesis failures are real bugs: fix the bug and add an `@example(...)` decorator. See [claude-reference.md](docs/reference/claude-reference.md).
- **Flaky tests**: NEVER skip/xfail/dismiss; fix fundamentally. FakeClock-first when the class accepts `clock=`. For "block until cancelled", use `asyncio.Event().wait()` not `asyncio.sleep(large)`.

## Git

- **Commits**: `<type>: <description>` (feat/fix/refactor/docs/test/chore/perf/ci); enforced by commitizen.
- **Signed commits**: required on every protected ref. GPG/SSH signed, OR GitHub App-signed via the `synthorg-repo-bot` (Git Data API `POST /git/commits` under installation token; produces `{verified: true, reason: "valid"}`). See [github-environments.md](docs/reference/github-environments.md#release_bot_app_).
- **Branches**: `<type>/<slug>` from main.
- **Pre-commit hooks**: see `.pre-commit-config.yaml`. Highlights: ruff, gitleaks, hadolint, no-em-dashes, no-redundant-timeout, check-single-migration-per-pr, check-no-modify-migration (bypass `SYNTHORG_MIGRATION_SQUASH=1`), no-release-please-token, workflow-shell-git-commits. `eslint-web` runs at pre-push only.
- **Hookify rules** (`.claude/hookify.*.md`): `block-pr-create` (must use `/pre-pr-review`), `block-double-push` (5-min throttle when an open PR exists; one-shot opt-out via `.claude/state/allow-double-push.flag` written by user out-of-band), `enforce-parallel-tests` (`-n 8`), `no-cd-prefix`, `no-local-coverage`.
- **Pre-push hooks**: mypy + pytest (affected modules) + golangci-lint + go vet + go test (CLI) + eslint-web + `orphan-fixtures` (opt-in via `SYNTHORG_CHECK_ORPHAN_FIXTURES=1`) + `setting-to-startup-trace` (conditional). Foundational module changes (core, config, observability) or conftest edits trigger full runs.
- **GitHub issue queries**: `gh issue list` via Bash, NOT MCP `list_issues` (unreliable field data).
- **Merge strategy**: squash. PR body becomes the squash commit message; trailers (`Release-As`, `Closes #N`) must be in the PR body to land.
- **PR issue references**: preserve existing `Closes #NNN`; never remove unless explicitly asked.

## Post-Implementation + Pre-PR Review (MANDATORY)

- After finishing an issue: branch (`<type>/<slug>`), commit, push. Do NOT auto-create a PR.
- ALWAYS use `/pre-pr-review` to create PRs (`gh pr create` is hookify-blocked). Trivial / docs-only: `/pre-pr-review quick`.
- After the PR exists, `/aurelio-review-pr` handles external reviewer feedback.
- Fix EVERYTHING valid review agents find, including pre-existing issues in surrounding code, suggestions, and findings adjacent to the PR's changes. No deferring, no "out of scope".
