# CLAUDE.md: SynthOrg

## Project

- **What**: Framework for building synthetic organizations (autonomous AI agents orchestrated as a virtual company)
- **Python**: 3.14+ (PEP 649 native lazy annotations)
- **License**: BUSL-1.1 with narrowed Additional Use Grant (free production use for non-competing small orgs; converts to Apache 2.0 three years after release)
- **Layout**: `src/synthorg/` (src layout), `tests/` (unit/integration/e2e), `web/` (React 19 dashboard), `cli/` (Go CLI binary)
- **Design**: [DESIGN_SPEC.md](docs/DESIGN_SPEC.md) (pointer to `docs/design/` pages)

## Design Spec (MANDATORY)

- **ALWAYS read the relevant `docs/design/` page** before implementing any feature or planning any issue. [DESIGN_SPEC.md](docs/DESIGN_SPEC.md) is a pointer file linking the design pages under `docs/design/`.
- The design spec is the **starting point** for architecture, data models, and behavior
- If implementation deviates from the spec (better approach found, scope evolved, etc.), **alert the user and explain why**; the user decides whether to proceed or update the spec
- Do NOT silently diverge; every deviation needs explicit user approval
- When a spec topic is referenced (e.g. "the Agents page" or "the Engine page's Crash Recovery section"), read the relevant `docs/design/` page before coding
- When approved deviations occur, update the relevant `docs/design/` page to reflect the new reality

## Planning (MANDATORY)

- Every implementation plan must be **presented to the user** for accept/deny before coding starts
- At **every phase** of planning and implementation, be critical: actively look for ways to improve the design in the spirit of what we're building (robustness, correctness, simplicity, future-proofing where it's free)
- Surface improvements as suggestions, not silent changes; the user decides
- **Prioritize issues by dependency order**, not priority labels; unblocked dependencies come first

## Diagrams in Documentation

- **D2** (`\`\`\`d2`): architecture diagrams, nested container layouts, complex entity relationships. Rendered at build time via `mkdocs-d2-plugin` (dagre layout). Requires the [D2 CLI](https://d2lang.com/tour/install) on `PATH` locally and in CI (pinned to v0.7.1 via `.github/workflows/pages.yml`).
- **Mermaid** (`\`\`\`mermaid`): flowcharts, sequence diagrams, simple hierarchies, pipelines. Rendered client-side via `pymdownx.superfences`.
- **Markdown tables**: grid/matrix data that is semantically tabular (not diagrams).
- D2 uses theme 200 (Dark Mauve), dark-only render, configured globally in `mkdocs.yml`.
- Never use `\`\`\`text` blocks with ASCII/Unicode box-drawing characters for diagrams.
- Review agent `diagram-syntax-validator` runs in `/pre-pr-review` and `/aurelio-review-pr` pipelines.

## Quick Commands

```bash
uv sync                                    # install all deps (dev + test)
uv sync --group docs                       # install docs toolchain (zensical + D2 plugins; required before zensical commands on first clone)
uv run ruff check src/ tests/              # lint
uv run ruff check src/ tests/ --fix        # lint + auto-fix
uv run ruff format src/ tests/             # format
uv run mypy src/ tests/                    # type-check (strict)
uv run python -m pytest tests/ -m unit -n 8            # unit tests only
uv run python -m pytest tests/ -m integration -n 8     # integration tests only
uv run python -m pytest tests/ -m e2e -n 8             # e2e tests only
uv run python -m pytest tests/ -n 8 --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80  # full suite + coverage (benchmarks excluded)
uv run python -m pytest tests/benchmarks/ --codspeed -n0  # Python perf benchmarks (CodSpeed CPU Simulation; -n0 required, pytest-codspeed runs serial)
HYPOTHESIS_PROFILE=dev uv run python -m pytest tests/ -m unit -n 8 -k properties   # property tests (dev, 1000 examples)
HYPOTHESIS_PROFILE=fuzz uv run python -m pytest tests/ -m unit -n 8 --timeout=0    # deep fuzzing (10,000 examples, no deadline, all @given tests)
uv run pre-commit run --all-files          # all pre-commit hooks
atlas migrate diff --env {sqlite,postgres} <name>   # generate migration from schema.sql diff (postgres needs Docker)
# atlas migrate validate / atlas schema diff / squash_migrations.sh -- see docs/guides/persistence-migrations.md
uv run python scripts/export_openapi.py    # export OpenAPI schema (needed before docs build)
uv run python scripts/generate_comparison.py  # generate comparison page (needed before docs build)
PYTHONPATH=. uv run zensical build          # build docs (output: _site/docs/) -- PYTHONPATH=. enables d2_fence.py for D2 rendering
uv run python scripts/patch_sitemap.py      # add non-Markdown assets (Scalar viewer) to the built sitemap (run after zensical build)
PYTHONPATH=. uv run zensical serve          # local docs preview (http://127.0.0.1:8000)
```

### Web Dashboard

See `web/CLAUDE.md` for commands, design system, and component inventory. The CI-matching full-suite leak check is `npm --prefix web run test -- --coverage --detect-async-leaks` (the `Leaks N leaks` summary must stay at or below the `MAX_ASYNC_LEAKS` ceiling defined in `.github/workflows/ci.yml`; any new store that schedules timers must expose a teardown hook per `web/CLAUDE.md`).

### CLI (Go Binary)

See `cli/CLAUDE.md` for commands, flags, and reference. Key rule: use `go -C cli` (never `cd cli`).

## Reference (load on demand)

- [docs/reference/claude-reference.md](docs/reference/claude-reference.md): Documentation layout, Docker commands, Package Structure, Releasing, CI pipelines, Dependencies, Hypothesis deep-dive
- [docs/reference/mcp-handler-contract.md](docs/reference/mcp-handler-contract.md): MCP tool handler protocol + envelope + guardrails
- [docs/reference/telemetry.md](docs/reference/telemetry.md): privacy allowlist, env resolution chain, Docker enrichment
- [docs/reference/pluggable-subsystems.md](docs/reference/pluggable-subsystems.md): canonical protocol/strategy/factory examples
- [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md): SEC-1 untrusted-content fences, HTML XXE, secret-log redaction
- [docs/reference/lifecycle-sync.md](docs/reference/lifecycle-sync.md): async start/stop lifecycle lock pattern
- [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md): persistence exception categories + service layer rules
- [docs/reference/conventions.md](docs/reference/conventions.md): implicit conventions (repository CRUD, lifecycle symmetry, response wrapping, validator mode default, event imports + inventory, domain error hierarchies, file structure, frozen ConfigDict, args models, Pydantic v2, async concurrency, Clock seam)
- [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md): full source matrix + exception registry + migration recipe
- [docs/reference/errors.md](docs/reference/errors.md): RFC 9457 error codes + HTTP exception handler registration
- [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md): currency / locale / timezone resolution chain + enforcement

## Web Dashboard Design System (MANDATORY)

Reuse components from `web/src/components/ui/` before creating new ones. Never hardcode hex colors, font-family, pixel spacing, Motion transitions, or BCP 47 locale strings; use design tokens, `@/lib/motion` presets, and the helpers in `@/utils/format`. Enforced by `scripts/check_web_design_system.py` (PostToolUse hook on every `web/src/` edit). See `web/CLAUDE.md` for the component inventory, token rules, and post-training references (TS6, Storybook 10).

## Regional Defaults (MANDATORY)

No default may privilege a single region, currency, or locale. Every user-facing format resolves from: user/company setting -> browser/system -> neutral fallback.

- **Currency**: never hardcode ISO 4217 codes or symbols. Backend: `DEFAULT_CURRENCY` from `synthorg.budget.currency` or the runtime `budget.currency` setting. Frontend: `DEFAULT_CURRENCY` from `@/utils/currencies` or `useSettingsStore().currency`.
- **Field naming**: no `_usd` suffix on money fields anywhere. The type carries money semantics; the value is in the operator's configured currency.
- **Locale**: never hardcode BCP 47 tags or call bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()`. Use helpers in `@/utils/format` which read `getLocale()` from `@/utils/locale`. The backend has no operator-tunable locale setting; backend `Intl` formatting uses the system locale plus the browser timezone. The `company.name_locales` list controls procedural-name generation only; it does not feed number / date / time formatting.
- **Timezone**: store UTC; render via `Intl` without passing `timeZone` (browser tz wins).
- **Date / number format**: always via `Intl`; no hand-rolled templates.
- **Units**: metric only. **Spelling**: International / British English UI default (`colour`, `behaviour`, `organise`, `centred`, `analyse`, `cancelled`); document deviations. Spelling here is an editorial / UI-copy decision **only**; it does not affect runtime locale-sensitive formatting. Numbers, dates, times, currencies, and units still resolve via the user / company / browser / system fallback through `@/utils/format`, `@/utils/locale`, `DEFAULT_CURRENCY`, and `useSettingsStore().currency`, with no contradiction to the locale-neutral defaults above.
- **Monetary models**: every cost-bearing Pydantic model carries `currency: CurrencyCode`; aggregation sites enforce a same-currency invariant (mixing raises `MixedCurrencyAggregationError`, HTTP 409).

Enforced by `scripts/check_web_design_system.py` (web edits), `scripts/check_backend_regional_defaults.py` (backend edits), and `scripts/check_forbidden_literals.py` (pre-push + CI). See [docs/reference/regional-defaults.md](docs/reference/regional-defaults.md) for the resolution chain, allowlisted files, monetary-model inventory, and the `# lint-allow: regional-defaults` per-line opt-out.

## Persistence Boundary (MANDATORY)

- `src/synthorg/persistence/` is the **only** place that may import `aiosqlite`, `sqlite3`, `psycopg`, or `psycopg_pool`, or emit raw SQL DDL/DML keywords in string literals. Enforced by `scripts/check_persistence_boundary.py` (pre-push + CI).
- Every durable feature MUST define a repository Protocol in `persistence/<domain>_protocol.py`, concrete impls under `persistence/{sqlite,postgres}/`, and be exposed on `PersistenceBackend`.
- Controllers and API endpoints access persistence through domain-scoped **service layers** (e.g. `ArtifactService`, `WorkflowService`, `MemoryService`, `CustomRulesService`, `UserService`, `ProjectService`, `SsrfViolationService`, `SettingsService`; list non-exhaustive), never directly into repositories. Services centralize audit logging and cross-repo orchestration; repositories **must not** log mutations themselves (enforced by `scripts/check_persistence_boundary.py`).
- Adding a migration: read `docs/guides/persistence-migrations.md` first. Do not hand-edit SQL or `atlas.sum`.
- Per-line opt-out: `# lint-allow: persistence-boundary -- <required justification>`.

See [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md) for the three sanctioned exception categories, in-memory fallback naming rules, and migration-hash guardrails.

## Configuration Precedence (MANDATORY)

For every mutable setting: **DB > env (`SYNTHORG_<NAMESPACE>_<KEY>`) > YAML > code default**, resolved through `SettingsService` / `ConfigResolver`. First-cold-read emits one INFO `settings.value.resolved` carrying `source` + `yaml_path`; subsequent reads stay at DEBUG.

Two sanctioned exceptions: **init-time only** (DB credentials, bootstrap secrets -- env-only, **no** registry entry) and **read-only post-init** (log directory, NATS URL, worker count -- registered with `read_only_post_init=True` for /settings discoverability; `SettingsService.set()` raises `SettingReadOnlyError`).

Direct `os.environ.get(...)` reads in application code outside startup are forbidden. New settings register in `src/synthorg/settings/definitions/<namespace>.py` and are consumed via `ConfigResolver.get_*`.

See [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md) for the full source matrix, exception registry, and migration recipe.

## Shell Usage

- **NEVER use `cd` in Bash commands**: the working directory is already set to the project root. Use absolute paths or run commands directly. Do NOT prefix commands with `cd C:/Users/Aurelio/synthorg &&`. Exception: `bash -c "cd <dir> && <cmd>"` is safe (runs in a child process, no cwd side effects). Use this for tools without a `-C` flag, e.g. `bash -c "cd web && npm install"` since `npm --prefix` is broken for bare `npm install`.
- **NEVER use Bash to write or modify files**: use the Write or Edit tools. Do not use `cat >`, `cat << EOF`, `echo >`, `echo >>`, `sed -i`, `python -c "open(...).write(...)"`, or `tee` to create or modify files (read-only/inspection uses like piping to stdout are fine). This applies to all files (plan files, config files, source code) and all subagents.

## Code Conventions

- **Comments explain WHY only, never origin/review/issue context**: a code comment answers one question: *why is this code shaped this way, that the next reader couldn't infer from the code itself?* Forbidden everywhere (source, tests, docstrings, commit-message bodies): reviewer citations (`pre-PR review #N`, `CodeRabbit at file:line`, `(#NNNN, CodeRabbit ...)`, `Round-N review id ...`); in-code issue/PR back-references (`(#1682)`, `(see PR #N)`, `fixes #N`, `as part of #N`); cryptic internal-taxonomy shorthand the reader can't decode standing alone (e.g. `SEC-1` naked in `src/`; taxonomy tags belong in `docs/design/` and `docs/reference/`, the rationale belongs spelled out in code); migration / rebrand framing (`ported from`, `renamed from`, `moved here in round 7`); round/iteration narrative (`round-2 review surfaced this`); self-evident restatements of the code. What stays: hidden constraints, subtle invariants, workarounds for specific upstream bugs (with stable bug-tracker URL, not internal issue back-refs), and why a non-obvious choice was made framed in terms the next reader can verify against the code. Such references go stale the moment line numbers shift, the review is resolved, or the round number stops mapping to anything.
- **No `from __future__ import annotations`**: Python 3.14 has PEP 649.
- **PEP 758 except syntax**: `except A, B:` (no parens) when not binding to a name; ruff enforces this on 3.14. `as exc` requires parens (`except (A, B) as exc:`).
- **Type hints**: all public functions, mypy strict mode.
- **Docstrings**: Google style, required on public classes / functions (ruff D rules).
- **Immutability**: create new objects, never mutate existing ones. Frozen Pydantic models for config/identity; for non-Pydantic registries use `copy.deepcopy()` at construction + `MappingProxyType` wrapping; deepcopy at system boundaries (tool execution, provider serialization, persistence).
- **Config vs runtime state**: frozen models for config/identity; separate mutable-via-copy models (`model_copy(update=...)`) for runtime state that evolves. Never mix static config and mutable runtime fields in one model.
- **Pydantic v2 conventions**: `ConfigDict(frozen=True, allow_inf_nan=False)` everywhere; `extra="forbid"` on request DTOs; `@computed_field` for derived values; `NotBlankStr` from `core.types` for identifier / name fields. See [docs/reference/conventions.md](docs/reference/conventions.md) §10.
- **Args models at every system boundary (#1611)**: every `BaseTool` subclass, MCP tool registration, A2A RPC method, and WebSocket event declares a typed Pydantic args model and is validated before dispatch. See [docs/reference/conventions.md](docs/reference/conventions.md) §9 for the inventory and [docs/reference/mcp-handler-contract.md](docs/reference/mcp-handler-contract.md) for the MCP-specific contract.
- **Typed-boundary helper**: every entry-point that ingests a dict payload from an external source (MCP handler args, JWT decode, WebSocket control message, audit-chain payload, A2A JSON-RPC params, settings security export) calls `parse_typed()` from `synthorg.api.boundary`. The helper accepts either a Pydantic model class or a `TypeAdapter` (for discriminated unions); it validates, emits `API_BOUNDARY_VALIDATION_FAILED` on failure with the boundary name + redacted error description + first 5 field locations + truncated flag, and re-raises `ValidationError` for the caller to translate into the appropriate HTTP / RPC / envelope response. The `boundary` label MUST be a hardcoded `LiteralString` -- never user-controlled. Phase 3 lint guard `scripts/check_boundary_typed.py` enforces the contract: a regression at any of the six registered (file, function) pairs fails pre-push and CI. See [docs/reference/typed-boundaries.md](docs/reference/typed-boundaries.md) for the full per-boundary inventory and the "Adding a new boundary" recipe.
- **Async concurrency**: prefer `asyncio.TaskGroup` for fan-out / fan-in. Wrap independent task bodies in `async def` helpers that catch `Exception` (re-raise only `MemoryError` / `RecursionError`) so one failure doesn't unwind the group. See [docs/reference/conventions.md](docs/reference/conventions.md) §11.
- **Time injection (Clock seam)**: classes that read time or sleep cooperatively take `clock: Clock | None = None` defaulting to `SystemClock()` (`synthorg.core.clock`); tests inject `FakeClock`. See [docs/reference/conventions.md](docs/reference/conventions.md) §12 for the replacement table and the legacy-callable carve-outs.
- **Lifecycle synchronization**: async `start()` / `stop()` services own a dedicated `self._lifecycle_lock`; timed-out stops mark the service unrestartable. See [docs/reference/lifecycle-sync.md](docs/reference/lifecycle-sync.md).
- **Untrusted-content fences (SEC-1)**: wrap attacker-controllable strings at LLM call sites via `wrap_untrusted()` from `synthorg.engine.prompt_safety`; append `untrusted_content_directive(tags)` to the enclosing system prompt. See [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md).
- **HTML parsing (SEC-1)**: never call `lxml.html.fromstring` on attacker input; use `HTMLParseGuard` (`synthorg.tools.html_parse_guard`). See [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md).
- **Pluggable subsystems**: cross-cutting subsystems follow protocol + strategy + factory + config discriminator with safe defaults. Services (which wrap repositories) are a distinct pattern. See [docs/reference/pluggable-subsystems.md](docs/reference/pluggable-subsystems.md).
- **Line length**: 88 (ruff). **Functions**: < 50 lines. **Files**: < 800 lines.
- **Errors**: handle explicitly, never swallow. Domain error families register a base-class entry in `EXCEPTION_HANDLERS` (`src/synthorg/api/exception_handlers.py`) so subtypes get correct status codes. See [docs/reference/errors.md](docs/reference/errors.md) §"HTTP exception handler registration".
- **Domain error class naming**: error classes in domain modules use `<Domain><Condition>Error` and inherit from `DomainError` (or a domain-scoped intermediate that itself inherits `DomainError`). Bare `Exception` / `RuntimeError` at domain boundaries is forbidden; domain errors flow through `EXCEPTION_HANDLERS` for centralised RFC 9457 routing. See `src/synthorg/core/domain_errors.py`.
- **Frozen by default**: every Pydantic model is `ConfigDict(frozen=True, ...)` unless documented otherwise. Mutations go through `model_copy(update=...)`, never direct attribute assignment. Runtime-state models that must mutate (rare) document the deviation in a module-level comment. 1040+ frozen models out of 1100+ BaseModel subclasses.
- **Repository CRUD vocabulary**: persistence repositories use `save(entity) -> None` (insert-or-update, idempotent), `get(id) -> Entity | None` (None on miss, never raises), `delete(id) -> bool` (True on removal, False if absent), `list_items(...) -> tuple[Entity, ...]` (paginated / filtered), and `query(...) -> tuple[Entity, ...]`. Query methods always return tuples, never lists. See [docs/reference/conventions.md](docs/reference/conventions.md) §14.
- **Validate** at system boundaries (user input, external APIs, config files).
- **Datetime marshalling in persistence**: round-trip ISO 8601 timestamps through `parse_iso_utc` / `format_iso_utc` from `synthorg.persistence._shared` (both reject naive datetimes); use `normalize_utc` for relaxed coercion on already-typed `datetime` inputs. See [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md) §"Shared helpers".

## Logging

- **Every business-logic module** has `from synthorg.observability import get_logger` then `logger = get_logger(__name__)`. Variable name is always `logger`. Carve-outs (e.g. `meta/mcp/handlers/common_logging.py` keying at a fixed string) are documented in the module docstring.
- **Never** use `import logging` / `logging.getLogger()` / `print()` in application code (exception: `observability/{setup,sinks,syslog_handler,http_handler,otlp_handler}.py` for handler-construction / bootstrap code).
- **Event names**: always import constants from `synthorg.observability.events.<domain>`; never use string literals. See [docs/reference/conventions.md](docs/reference/conventions.md) §13 for the domain inventory and the `events/telemetry.py` namespace split (`TELEMETRY_*` log events vs `TELEMETRY_EVENT_*` payload types).
- **Structured kwargs**: always `logger.info(EVENT, key=value)`; never `logger.info("msg %s", val)`.
- **All error paths** log at WARNING or ERROR with context before raising.
- **State transitions**: every hop on a status enum (including non-terminal hops like `PENDING -> RUNNING`) logs at INFO using a domain-scoped `*_STATUS_TRANSITIONED` constant carrying `from_status` / `to_status` / domain id, AFTER the persistence write succeeds. See [docs/reference/conventions.md](docs/reference/conventions.md) §13.
- **DEBUG** for object creation, internal flow, entry/exit of key functions. Pure data models, enums, re-exports do NOT need logging.
- **Source-of-resolution audit**: every settings read emits one INFO `settings.value.resolved` event on first cold read per process. See [docs/reference/configuration-precedence.md](docs/reference/configuration-precedence.md).
- **Secret-log redaction (SEC-1)**: never call any `logger` severity (`exception` / `warning` / `error` / `info` / `debug`) with `error=str(exc)`; use structured logging with `error_type=type(exc).__name__` and `error=safe_error_description(exc)` (`from synthorg.observability import safe_error_description`), keeping severity (`warning` vs `error`) appropriate to context per the "All error paths log at WARNING or ERROR" rule above. All five log methods are enforced unconditionally by `scripts/check_logger_exception_str_exc.py`; the gate's AST matcher walks the `error=` value subtree, so wrapped forms (`str(exc)[:200]`, `str(exc) or fallback`, `str(exc) if cond else fallback`, f-string and `BinOp` concatenation) are also rejected. See [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md).

## MCP Handler Layer

SynthOrg exposes 200+ tools across 15 domain modules under `src/synthorg/meta/mcp/domains/`. Implementing a handler means: define the `ToolHandler` in `src/synthorg/meta/mcp/handlers/<domain>.py`, declare an `args_model` so the invoker validates before dispatch (#1611), call `require_destructive_guardrails(arguments, actor)` on any `admin_tool`, route through service-layer facades (never `app_state.persistence.*` directly), and emit the three log paths via the `common_logging` helpers (`log_handler_argument_invalid` / `log_handler_invoke_failed` / `log_handler_guardrail_violated`).

See [docs/reference/mcp-handler-contract.md](docs/reference/mcp-handler-contract.md) for the full contract (envelopes, helper modules, registries, domain codes, the legacy `common_args` path), `docs/design/tools.md` §"SynthOrg MCP Tool Surface" for the user-facing surface, and `docs/design/observability.md` §"MCP handler events" for the event inventory.

## Telemetry (Product)

Opt-in, off by default. Every event property must be explicitly listed in `_ALLOWED_PROPERTIES` keyed by event type; unknown keys raise `PrivacyViolationError` and are dropped. Never bypass the scrubber.

See [docs/reference/telemetry.md](docs/reference/telemetry.md) for enable flags, the 4-step environment resolution chain, forbidden key patterns, Docker daemon enrichment fields, and the add-new-property checklist.

## Resilience

- **All provider calls** go through `BaseCompletionProvider` which applies retry + rate limiting automatically
- **Never** implement retry logic in driver subclasses or calling code; it's handled by the base class
- **RetryConfig** and **RateLimiterConfig** are set per-provider in `ProviderConfig`
- **Retryable errors** (`is_retryable=True`): `RateLimitError`, `ProviderTimeoutError`, `ProviderConnectionError`, `ProviderInternalError`
- **Non-retryable errors** raise immediately without retry
- **`RetryExhaustedError`** signals that all retries failed; the engine layer catches this to trigger fallback chains
- **Rate limiter** respects `RateLimitError.retry_after` from providers, automatically pausing future requests
- **WebSocket per-frame timeout (DoS prevention)**: silent clients are closed with policy code 1008 once they exceed `api.ws_frame_timeout_seconds` (default 30s) without sending a frame. Wraps `socket.receive_text()` in `asyncio.wait_for(...)` so a connected-but-silent peer cannot indefinitely hold a slot.
- **WebSocket revalidation sliding window**: persistence-backend failures during the periodic revalidation are tracked via a `_SlidingWindowRateLimiter` (`api.ws_revalidation_window_seconds` default 60s, `api.ws_revalidation_max_failures` default 5) instead of a reset-on-success streak counter. A flaky persistence layer that returns one good response between every failure cluster cannot indefinitely keep stale-auth connections alive; once the window saturates, the socket closes with code 4011.

## Test Regression (MANDATORY)

When tests fail due to timeout, slowness, or xdist resource contention:
- **NEVER** delete tests, skip tests, or mark them `xfail` to "fix" slowness
- **NEVER** use `--no-verify` to bypass pre-push hooks
- **NEVER** modify `tests/baselines/unit_timing.json`; baseline updates require explicit user approval (enforced by `scripts/check_no_edit_baseline.sh` PreToolUse hook)
- **FIRST** run: `uv run python -m pytest tests/unit/ -m unit -n 8 --durations=50 --durations-min=0.5 -q --no-header` to identify the slow tests
- **THEN** compare against `tests/baselines/unit_timing.json` (the known-good baseline)
- **IF** suite time exceeds `baseline * 1.3`: this is a **source code regression**, not a test bug. Fix the source code that caused the regression, not the tests
- The `pytest_sessionfinish` hook in `tests/conftest.py` will warn loudly if a regression is detected; trust the warning

## Testing

- **Markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.slow`.
- **Mock-spec gate (#1604)**: every `Mock()` / `AsyncMock()` / `MagicMock()` in `tests/` MUST declare the interface it stands for via `spec=ConcreteClass` (Protocol or class). A pre-commit gate (`scripts/check_mock_spec.py`) blocks new bare-call sites; pre-existing sites are frozen in `scripts/mock_spec_baseline.txt`. Regenerate the baseline only via `uv run python scripts/check_mock_spec.py --update` and commit the diff so the change is reviewable. Without `spec=` the mock silently absorbs every attribute access, so production code that renames or drops a method passes every test (the original "mock drift" finding from #1604).
- **Shared mocks**: `tests/conftest.py` exposes `mock_dispatcher` (an `AsyncMock(spec=NotificationDispatcher)` covering `register` / `start` / `aclose` / `dispatch`); use it instead of building the spec inline.
- **Time-driven tests**: import `FakeClock` from `tests._shared.fake_clock` (NOT from any rollout-subsystem path) and inject it into the class under test. `FakeClock.sleep` advances virtual time AND yields once via `asyncio.sleep(0)` so cancellation on awaiting tasks propagates the same way it does under `SystemClock`. For tests that need to drive cooperative tasks waiting on the loop, `await clock.advance_async(seconds)`. **FakeClock-first: patch `time.monotonic()` / `asyncio.sleep()` globals only for legacy code paths that don't have a `Clock` seam (see `## Code Conventions` for the legacy-Callable list); when the class under test accepts a `clock=` parameter, always inject `FakeClock` rather than monkey-patching globals**.
- **Performance benchmarks** under `tests/benchmarks/` use `@pytest.mark.benchmark` (registered for pytest-codspeed). Benches are intentionally NOT marked `unit`; `pytest -m unit -n 8` skips them and the `codspeed-python` job in `.github/workflows/codspeed.yml` runs them via `--codspeed`. The same workflow runs the React-dashboard benches in a parallel `codspeed-web` job (Sharded Benchmarks contract; both shards must live in the same workflow run for CodSpeed to aggregate them into one PR report). Locally, run `uv run python -m pytest tests/benchmarks/ --codspeed -n0`.
- **Heap-ceiling tests** (peak-heap assertions via `tracemalloc`) live under `tests/unit/perf/` and are marked `@pytest.mark.unit` because they are real assertions on a captured peak-heap value, not throughput measurements. They run on every `pytest -m unit -n 8` invocation.
- **Coverage**: 80% minimum (enforced in CI; benchmarks are excluded via `--ignore=tests/benchmarks/` in coverage runs)
- **Async**: `asyncio_mode = "auto"`; no manual `@pytest.mark.asyncio` needed
- **Timeout**: 30 seconds per test (global in `pyproject.toml`; do not add per-file `pytest.mark.timeout(30)` markers; non-default overrides like `timeout(60)` are allowed)
- **Parallelism**: `pytest-xdist` via `-n 8`, distribution `--dist=loadfile` (default in `pyproject.toml addopts`). **ALWAYS** include `-n 8` when running pytest locally, never run tests sequentially. CI uses `-n auto` (fewer cores on runners). `loadfile` keeps every test in a file pinned to the same xdist worker, which prevents the cumulative resource leak seen under `worksteal` on Python 3.14 + Windows where mixed-fixture redistribution exhausts ProactorEventLoop socket finalisers and crashes a worker mid-suite.
- **Isolation regression gate**: the affected-tests pre-push runner (`scripts/run_affected_tests.py`) runs the affected subset twice via `pytest-repeat` (`--count 2 -x`) after the primary green pass. This catches the next "module-level state leaks across xdist workers" offender at PR time rather than weeks later. Opt out via `SYNTHORG_SKIP_ISOLATION_GATE=1` for emergency pushes only.
- **Logger spying antipattern**: never use `monkeypatch.setattr(module.logger, "info", spy)`. The `BoundLoggerLazyProxy` returned by `get_logger(__name__)` serves log methods via `__getattr__` (per-call rebind) and stores nothing on the instance dict. `monkeypatch.setattr` snapshots the bound method `getattr` returns and "restores" it via `setattr` at undo, **permanently caching** that stale bound method into `proxy.__dict__`. Subsequent `structlog.testing.capture_logs()` calls then route around it because `__getattr__` is shadowed. Use a context manager that wraps direct `setattr` + `try/finally del proxy.<level>` instead -- see `_logger_info_spy` in `tests/unit/settings/test_service.py` for the canonical pattern.
- **Parametrize**: Prefer `@pytest.mark.parametrize` for testing similar cases
- **Vendor-agnostic everywhere**: NEVER use real vendor names (Anthropic, OpenAI, Claude, GPT, etc.) in project-owned code, docstrings, comments, tests, or config examples. Use generic names: `example-provider`, `example-large-001`, `example-medium-001`, `example-small-001`, `large`/`medium`/`small` as aliases. Vendor names may only appear in: (1) `.claude/` skill/agent files, (2) third-party import paths/module names (e.g. `litellm.types.llms.openai`), (3) provider presets (`src/synthorg/providers/presets.py`) which are user-facing runtime data, (4) provider logo assets (`web/public/provider-logos/*.svg` plus the matching README) -- the SVG filenames mirror the preset names by necessity, and `<ProviderLogo>` falls back to a generic icon if a file is absent. Tests must use `test-provider`, `test-small-001`, etc.
- **Property-based testing**: Python uses [Hypothesis](https://hypothesis.readthedocs.io/), React uses [fast-check](https://fast-check.dev/), Go uses `testing.F` fuzz functions. CI runs 10 deterministic examples per property test (`derandomize=True`, no flakes). When Hypothesis finds a failure, it is a **real bug**: read the shrunk example, fix the underlying bug, and add an explicit `@example(...)` decorator so the case is permanently covered. See [docs/reference/claude-reference.md](docs/reference/claude-reference.md) §"Property-based Testing (Hypothesis): Deep Dive" for profile catalog, local fuzzing commands, and failure-handling workflow.
- **Flaky tests**: NEVER skip, dismiss, or ignore flaky tests; always fix them fully and fundamentally. For timing-sensitive tests, the **FakeClock-first** rule applies: if the class under test accepts a `clock=` parameter, inject `FakeClock` from `tests._shared.fake_clock` and drive virtual time via `clock.advance(...)` / `await clock.advance_async(...)`. Patch `time.monotonic()` / `asyncio.sleep()` at the module level only for legacy code paths that don't yet have a `Clock` seam. For tasks that must block indefinitely until cancelled (e.g. simulating a slow provider or stubborn coroutine), use `asyncio.Event().wait()` instead of `asyncio.sleep(large_number)`; it is cancellation-safe and carries no timing assumptions.

## Git

- **Commits**: `<type>: <description>`. Types: feat, fix, refactor, docs, test, chore, perf, ci
- **Enforced by**: commitizen (commit-msg hook)
- **Signed commits**: required on `main` via branch protection (and on every other ref under the default ruleset, including `cla-signatures`). All commits must be GPG/SSH signed. Exception: **GitHub App-signed commits from the `synthorg-repo-bot`** also satisfy `required_signatures`. These are minted via the Git Data API (`POST /git/commits`) under the App installation token, so GitHub attaches a bot signature that verifies as `{verified: true, reason: "valid"}` even though no GPG/SSH key is in play. Used by `release.yml`, `dev-release.yml`, `auto-rollover.yml`, `graduate.yml`, and `cla.yml:cla-sign`; see [docs/reference/github-environments.md](docs/reference/github-environments.md#release_bot_app_).
- **Branches**: `<type>/<slug>` from main
- **Pre-commit hooks**: trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-json, check-merge-conflict, check-added-large-files, no-commit-to-branch (main), ruff check+format, gitleaks, hadolint (Dockerfile linting), golangci-lint + go vet (CLI, conditional on `cli/**/*.go`), no-em-dashes, no-redundant-timeout, check-single-migration-per-pr (at most 1 new migration per backend per PR), check-no-modify-migration (block editing existing migrations; bypass with `SYNTHORG_MIGRATION_SQUASH=1`), no-release-please-token (#1555: forbids new `RELEASE_PLEASE_TOKEN` references in any `.github/` YAML), workflow-shell-git-commits (#1555: scoped to `.github/workflows/*.yml`; blocks every shell `git commit + git push` pair in the same `run:` block, unconditionally. Local pushes never produce signed commits, so an App-token mint elsewhere in the job does not sanitise them; writes MUST go through the Git Data API). **Note**: `eslint-web` runs at **pre-push only** (see Pre-push hooks below) because TypeScript project-graph boot is 15-30s, so gating it on every commit penalises backend-only work.
- **Hookify rules** (committed in `.claude/hookify.*.md`):
  - `block-pr-create`: blocks direct `gh pr create` (must use `/pre-pr-review`)
  - `block-double-push`: blocks a second `git push` within the 5-min throttle window **when an open PR exists for the current branch** (outside a PR, normal feature pushes are unthrottled). Throttle is split across two hooks: `scripts/check_push_throttle.sh` runs as a PreToolUse-Bash hook and reads the timestamp; the companion `scripts/record_push_throttle.sh` runs as a PostToolUse-Bash hook and only writes the timestamp when `git push` actually exited 0 -- a push rejected by another PreToolUse hook (mypy, eslint, ruff format, ...) does not consume the window. Override is one-shot and out-of-band: the user must create `.claude/state/allow-double-push.flag` in their own shell with the current branch name as the first line, e.g. `printf '%s\n' "$(git branch --show-current)" >.claude/state/allow-double-push.flag && git push <args>`. The flag is consumed (deleted) on use. The model cannot create the flag itself: `scripts/check_no_throttle_override_creation.sh` is registered as a PreToolUse hook on `Bash|Write|Edit|NotebookEdit` and rejects any tool call that references the flag path. Tunable via `SYNTHORG_PUSH_THROTTLE_MIN` (minutes); state file `.claude/state/last-push.json`, gitignored.
  - `enforce-parallel-tests`: enforces `-n 8` with pytest
  - `no-cd-prefix`: blocks `cd` prefix in Bash commands
  - `no-local-coverage`: blocks `--cov` flags locally (CI handles coverage)
- **Pre-push hooks**: mypy type-check (affected modules only) + pytest unit tests (affected modules only) + golangci-lint + go vet + go test (CLI, conditional on `cli/**/*.go`) + eslint-web (web dashboard) + `orphan-fixtures` (opt-in via `SYNTHORG_CHECK_ORPHAN_FIXTURES=1`; silent no-op otherwise) (fast gate before push, skipped in pre-commit.ci because dedicated CI jobs already run these). Foundational module changes (core, config, observability) or conftest changes trigger full runs.
- **Pre-commit.ci**: autoupdate disabled (`autoupdate_schedule: never`); Renovate owns hook version bumps via `pre-commit` manager
- **GitHub issue queries**: use `gh issue list` via Bash (not MCP tools); MCP `list_issues` has unreliable field data
- **Merge strategy**: squash merge. PR body becomes the squash commit message on main. Trailers (e.g. `Release-As`, `Closes #N`) must be in the PR body to land in the final commit.
- **PR issue references**: preserve existing `Closes #NNN` references; never remove unless explicitly asked

## Post-Implementation (MANDATORY)

- **After finishing an issue implementation**: always create a feature branch (`<type>/<slug>`), commit, and push; do NOT create a PR automatically
- Do NOT leave work uncommitted on main; branch, commit, push immediately after finishing

## Pre-PR Review (MANDATORY)

- **NEVER create a PR directly**: `gh pr create` is blocked by hookify
- **ALWAYS use `/pre-pr-review`** to create PRs; it runs automated checks + review agents + fixes before creating the PR
- For trivial/docs-only changes: `/pre-pr-review quick` skips agents but still runs automated checks
- After the PR exists, use `/aurelio-review-pr` to handle external reviewer feedback
- The `/commit-push-pr` command is effectively blocked (it calls `gh pr create` internally)
- **Fix everything valid, never skip**: When review agents find valid issues (including pre-existing issues in surrounding code, suggestions, and findings adjacent to the PR's changes), fix them all. No deferring, no "out of scope" skipping.
