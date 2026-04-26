# CLAUDE.md -- SynthOrg

## Project

- **What**: Framework for building synthetic organizations -- autonomous AI agents orchestrated as a virtual company
- **Python**: 3.14+ (PEP 649 native lazy annotations)
- **License**: BUSL-1.1 with narrowed Additional Use Grant (free production use for non-competing small orgs; converts to Apache 2.0 three years after release)
- **Layout**: `src/synthorg/` (src layout), `tests/` (unit/integration/e2e), `web/` (React 19 dashboard), `cli/` (Go CLI binary)
- **Design**: [DESIGN_SPEC.md](docs/DESIGN_SPEC.md) (pointer to `docs/design/` pages)

## Design Spec (MANDATORY)

- **ALWAYS read the relevant `docs/design/` page** before implementing any feature or planning any issue. [DESIGN_SPEC.md](docs/DESIGN_SPEC.md) is a pointer file linking to the 20 design pages.
- The design spec is the **starting point** for architecture, data models, and behavior
- If implementation deviates from the spec (better approach found, scope evolved, etc.), **alert the user and explain why** -- user decides whether to proceed or update the spec
- Do NOT silently diverge -- every deviation needs explicit user approval
- When a spec topic is referenced (e.g. "the Agents page" or "the Engine page's Crash Recovery section"), read the relevant `docs/design/` page before coding
- When approved deviations occur, update the relevant `docs/design/` page to reflect the new reality

## Planning (MANDATORY)

- Every implementation plan must be **presented to the user** for accept/deny before coding starts
- At **every phase** of planning and implementation, be critical -- actively look for ways to improve the design in the spirit of what we're building (robustness, correctness, simplicity, future-proofing where it's free)
- Surface improvements as suggestions, not silent changes -- user decides
- **Prioritize issues by dependency order**, not priority labels -- unblocked dependencies come first

## Diagrams in Documentation

- **D2** (`\`\`\`d2`): architecture diagrams, nested container layouts, complex entity relationships. Rendered at build time via `mkdocs-d2-plugin` (dagre layout). Requires the [D2 CLI](https://d2lang.com/tour/install) on `PATH` locally and in CI (pinned to v0.7.1 via `.github/workflows/pages.yml`).
- **Mermaid** (`\`\`\`mermaid`): flowcharts, sequence diagrams, simple hierarchies, pipelines. Rendered client-side via `pymdownx.superfences`.
- **Markdown tables**: grid/matrix data that is semantically tabular (not diagrams).
- D2 uses theme 200 (Dark Mauve), dark-only render -- configured globally in `mkdocs.yml`.
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
uv run python -m pytest tests/ -n 8 --cov=synthorg --cov-fail-under=80  # full suite + coverage
HYPOTHESIS_PROFILE=dev uv run python -m pytest tests/ -m unit -n 8 -k properties   # property tests (dev, 1000 examples)
HYPOTHESIS_PROFILE=fuzz uv run python -m pytest tests/ -m unit -n 8 --timeout=0    # deep fuzzing (10,000 examples, no deadline, all @given tests)
uv run pre-commit run --all-files          # all pre-commit hooks
atlas migrate diff --env sqlite <name>     # generate SQLite migration from schema.sql diff
atlas migrate diff --env postgres <name>   # generate Postgres migration (requires Docker for dev DB)
atlas migrate validate --dir "file://src/synthorg/persistence/sqlite/revisions"    # validate SQLite migration checksums
atlas migrate validate --dir "file://src/synthorg/persistence/postgres/revisions"  # validate Postgres migration checksums
atlas schema diff --env sqlite             # drift detection for SQLite (schema.sql vs revisions)
atlas schema diff --env postgres           # drift detection for Postgres (schema.sql vs revisions)
bash scripts/squash_migrations.sh          # squash old migrations (release-time)
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

## Web Dashboard Design System (MANDATORY)

See `web/CLAUDE.md` for the full component inventory, design token rules, and post-training references (TS6, Storybook 10). Key rules:
- **ALWAYS reuse** existing components from `web/src/components/ui/` before creating new ones
- **NEVER hardcode** hex colors, font-family, pixel spacing, or Motion transitions -- use design tokens and `@/lib/motion` presets
- **NEVER hardcode** BCP 47 locale strings (e.g. `'en-US'`) or call bare `.toLocaleString()` -- use the helpers in `@/utils/format` which read `getLocale()` from `@/utils/locale`
- A PostToolUse hook (`scripts/check_web_design_system.py`) enforces these rules on every Edit/Write to `web/src/` (colors, fonts, Motion durations, hardcoded locales, bare `.toLocale*String()` calls, missing Storybook stories, duplicate component patterns)

## Regional Defaults (MANDATORY)

No default may privilege a single region, currency, or locale. Every user-facing format resolves from: user/company setting -> browser/system -> neutral fallback.

- **Currency**: never hardcode ISO 4217 codes (`'USD'`, `'EUR'`, `'GBP'`, ...) or symbols (`$`, `€`, `£`) in production code. Backend: use `DEFAULT_CURRENCY` from `synthorg.budget.currency` or read the runtime `budget.currency` setting. Frontend: import `DEFAULT_CURRENCY` from `@/utils/currencies` or read `useSettingsStore().currency`. Allowlisted files: the backend symbol table (`budget/currency.py`), the dropdown options (`web/src/utils/currencies.ts`), and the `DEFAULT_CURRENCY` re-export in `format.ts`.
- **Field naming**: no `_usd` suffix on money fields (backend, API DTOs, TS types, DB columns). The field value is in the operator's configured currency; the type carries the money semantics, not the name.
- **Locale**: never hardcode BCP 47 tags (`'en-US'`, `'de-DE'`, ...) or call bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()`. Use helpers in `@/utils/format`, all of which read `getLocale()` from `@/utils/locale`. Frontend fallback is plain `'en'` (neutral English). Backend exposes a `display.locale` setting that overrides the browser default when set.
- **Timezone**: store UTC everywhere; render in the user's browser timezone via `Intl` without passing a `timeZone` option.
- **Date / number format**: always via `Intl`; no `MM/DD/YYYY` / `DD/MM/YYYY` / comma-separator templates.
- **Units**: metric only. Paper size A4 if print flows are ever added.
- **Spelling**: American English is the UI default (`color`, `initialize`). Document deviations; do not mix.

- **Monetary models**: every cost-bearing Pydantic model (``CostRecord``, ``TaskMetricRecord``, ``LlmCalibrationRecord``, ``AgentRuntimeState``) carries ``currency: CurrencyCode`` (ISO 4217, validated against the allowlist in ``synthorg.budget.currency``). Every aggregation site (``CostTracker``, ``ReportGenerator``, ``CostOptimizer``, HR ``WindowMetrics``) enforces a same-currency invariant; mixing currencies raises ``MixedCurrencyAggregationError`` (HTTP 409).

Enforced by `scripts/check_web_design_system.py` (PostToolUse hook on every `web/src/` edit) for the frontend surface, and by `scripts/check_backend_regional_defaults.py` (PostToolUse hook on every `src/synthorg/` edit) for the Python backend. Both hooks flag hardcoded currency codes, currency symbols adjacent to digits (`"$10"`, `"\u20ac50"`), identifiers ending in `_usd`, BCP 47 locale literals (`'en-US'`), and `localhost:<port>` in application code. Legitimate opt-outs use a `# lint-allow: regional-defaults` (Python) or `// lint-allow: regional-defaults` (TS) marker on or above the line. A stricter CI-gated `scripts/check_forbidden_literals.py` runs in pre-push and GitHub Actions to catch the same issues on non-Claude commits.

## Persistence Boundary (MANDATORY)

- `src/synthorg/persistence/` is the **only** place that may import `aiosqlite`, `sqlite3`, `psycopg`, or `psycopg_pool`, or emit raw SQL DDL/DML keywords in string literals. Enforced by `scripts/check_persistence_boundary.py` (pre-push + CI).
- Every durable feature MUST define a repository Protocol in `persistence/<domain>_protocol.py`, concrete impls under `persistence/{sqlite,postgres}/`, and be exposed on `PersistenceBackend`.
- Controllers and API endpoints access persistence through domain-scoped **service layers** (e.g. `ArtifactService`, `WorkflowService`, `MemoryService`, `CustomRulesService`, `UserService`, `ProjectService`, `SsrfViolationService`, `SettingsService` -- non-exhaustive), never directly into repositories. Services centralize audit logging and cross-repo orchestration; repositories **must not** log mutations themselves (enforced by `scripts/check_persistence_boundary.py`).
- Adding a migration: read `docs/guides/persistence-migrations.md` first. Do not hand-edit SQL or `atlas.sum`.
- Per-line opt-out: `# lint-allow: persistence-boundary -- <required justification>`.

See [docs/reference/persistence-boundary.md](docs/reference/persistence-boundary.md) for the three sanctioned exception categories, in-memory fallback naming rules, and migration-hash guardrails.

## Shell Usage

- **NEVER use `cd` in Bash commands** -- the working directory is already set to the project root. Use absolute paths or run commands directly. Do NOT prefix commands with `cd C:/Users/Aurelio/synthorg &&`. Exception: `bash -c "cd <dir> && <cmd>"` is safe (runs in a child process, no cwd side effects). Use this for tools without a `-C` flag -- e.g. `bash -c "cd web && npm install"` since `npm --prefix` is broken for bare `npm install`.
- **NEVER use Bash to write or modify files** -- use the Write or Edit tools. Do not use `cat >`, `cat << EOF`, `echo >`, `echo >>`, `sed -i`, `python -c "open(...).write(...)"`, or `tee` to create or modify files (read-only/inspection uses like piping to stdout are fine). This applies to all files (plan files, config files, source code) and all subagents.

## Code Conventions

- **No `from __future__ import annotations`** -- Python 3.14 has PEP 649
- **PEP 758 except syntax**: use `except A, B:` (no parentheses) when NOT binding to a name -- ruff enforces this on Python 3.14. When binding via `as exc`, parentheses are still required (`except (A, B) as exc:`) because Python 3.14's grammar forbids the unparenthesized form with `as`.
- **Type hints**: all public functions, mypy strict mode
- **Docstrings**: Google style, required on public classes/functions (enforced by ruff D rules)
- **Immutability**: create new objects, never mutate existing ones. For non-Pydantic internal collections (registries, `BaseTool`), use `copy.deepcopy()` at construction + `MappingProxyType` wrapping for read-only enforcement. For `dict`/`list` fields in frozen Pydantic models, rely on `frozen=True` for field reassignment prevention and `copy.deepcopy()` at system boundaries (tool execution, LLM provider serialization, inter-agent delegation, serializing for persistence).
- **Config vs runtime state**: frozen Pydantic models for config/identity; separate mutable-via-copy models (using `model_copy(update=...)`) for runtime state that evolves (e.g. agent execution state, task progress). Never mix static config fields with mutable runtime fields in one model.
- **Models**: Pydantic v2 (`BaseModel`, `model_validator`, `computed_field`, `ConfigDict`). Adopted conventions: use `allow_inf_nan=False` in all `ConfigDict` declarations to reject `NaN`/`Inf` in numeric fields at validation time; use `@computed_field` for derived values instead of storing + validating redundant fields (e.g. `TokenUsage.total_tokens`); use `NotBlankStr` (from `core.types`) for all identifier/name fields -- including optional (`NotBlankStr | None`) and tuple (`tuple[NotBlankStr, ...]`) variants -- instead of manual whitespace validators.
- **Async concurrency**: prefer `asyncio.TaskGroup` for fan-out/fan-in parallel operations in new code (e.g. multiple tool invocations, parallel agent calls). Prefer structured concurrency over bare `create_task`. Existing code is being migrated incrementally. When running multiple tasks inside a `TaskGroup` where one task's failure should NOT cancel the others (independent workers, classification detectors, notification sinks), wrap each task body in a small `async def` helper that catches `Exception` and returns a safe default (re-raising only `MemoryError`/`RecursionError`); never let a single worker abort the whole group.
- **Lifecycle synchronization**: services with async `start()` / `stop()` use a dedicated `self._lifecycle_lock: asyncio.Lock` (separate from any hot-path lock) held across the full body of both methods; timed-out stops must mark the service unrestartable. See [docs/reference/lifecycle-sync.md](docs/reference/lifecycle-sync.md) for the full rule and canonical examples.
- **Untrusted-content fences at LLM call sites (SEC-1)**: any attacker-controllable string interpolated into an LLM prompt must be wrapped via `wrap_untrusted(tag, content)` from `synthorg.engine.prompt_safety`, and the enclosing system prompt must append `untrusted_content_directive(tags)`. See [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md) for the standard tag set, key reference call sites, and the tool-result injection detector.
- **HTML parsing (SEC-1)**: never call `lxml.html.fromstring` directly on attacker-controlled input; use `HTMLParseGuard` in `synthorg.tools.html_parse_guard`. See [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md) for the pre-scan / parser configuration details.
- **Pluggable subsystems**: new cross-cutting subsystems follow a protocol + strategy + factory + config discriminator pattern with safe defaults. See [docs/reference/pluggable-subsystems.md](docs/reference/pluggable-subsystems.md) for the rule and canonical examples (classification, verification, Chief of Staff, telemetry, rollout, rate limits, escalation queue). Note: the **service layer** (see Persistence Boundary) is a distinct pattern; services wrap repositories to keep controllers thin, while the Protocol+Strategy+Factory+Config machinery applies only to subsystems with multiple interchangeable runtime-selectable implementations.
- **Line length**: 88 characters (ruff)
- **Functions**: < 50 lines, files < 800 lines
- **Errors**: handle explicitly, never silently swallow
- **Validate**: at system boundaries (user input, external APIs, config files)

## Logging

- **Every module** with business logic MUST have: `from synthorg.observability import get_logger` then `logger = get_logger(__name__)`
- **Never** use `import logging` / `logging.getLogger()` / `print()` in application code (exception: `observability/setup.py`, `observability/sinks.py`, `observability/syslog_handler.py`, `observability/http_handler.py`, and `observability/otlp_handler.py` may use stdlib `logging` and `print(..., file=sys.stderr)` for handler construction, bootstrap, and error reporting code that runs before or during logging system configuration)
- **Variable name**: always `logger` (not `_logger`, not `log`)
- **Event names**: always use constants from the domain-specific module under `synthorg.observability.events` (e.g., `API_REQUEST_STARTED` from `events.api`, `TOOL_INVOKE_START` from `events.tool`). Each domain has its own module -- see `src/synthorg/observability/events/` for the full inventory of constants. Import directly: `from synthorg.observability.events.<domain> import EVENT_CONSTANT`
- **Structured kwargs**: always `logger.info(EVENT, key=value)` -- never `logger.info("msg %s", val)`
- **All error paths** must log at WARNING or ERROR with context before raising
- **All state transitions** must log at INFO
- **DEBUG** for object creation, internal flow, entry/exit of key functions
- Pure data models, enums, and re-exports do NOT need logging
- **Secret-log redaction (SEC-1)**: on credential-bearing paths (OAuth, secret backends, settings encryption, A2A client/gateway, API auth middleware, persistence repos), never use `logger.exception(EVENT, error=str(exc))`; use `logger.warning(EVENT, error_type=type(exc).__name__, error=safe_error_description(exc))` from `synthorg.observability` instead. A pre-commit gate (`scripts/check_logger_exception_str_exc.py`) blocks new violations above baseline. See [docs/reference/sec-prompt-safety.md](docs/reference/sec-prompt-safety.md) for the full rule, the `scrub_event_fields` belt-and-braces masking, and the gate's detection semantics.

## MCP Handler Layer

SynthOrg exposes 200+ tools across 15 domains via its MCP server. Adding a new handler means implementing the `ToolHandler` protocol in `src/synthorg/meta/mcp/handlers/<domain>.py`. Handlers use envelope helpers (`ok`/`err`/`capability_gap`/`not_supported`) from `common.py`, validate args via `require_arg`, and run `require_destructive_guardrails(arguments, actor)` on any `admin_tool`. Handlers route through service-layer facades, never into `app_state.persistence.*` directly.

See [docs/reference/mcp-handler-contract.md](docs/reference/mcp-handler-contract.md) for the full contract, `docs/design/tools.md` §"SynthOrg MCP Tool Surface" for the user-facing surface, and `docs/design/observability.md` §"MCP handler events" for the event inventory.

## Telemetry (Product)

Opt-in, off by default. Every event property must be explicitly listed in `_ALLOWED_PROPERTIES` keyed by event type; unknown keys raise `PrivacyViolationError` and are dropped. Never bypass the scrubber.

See [docs/reference/telemetry.md](docs/reference/telemetry.md) for enable flags, the 4-step environment resolution chain, forbidden key patterns, Docker daemon enrichment fields, and the add-new-property checklist.

## Resilience

- **All provider calls** go through `BaseCompletionProvider` which applies retry + rate limiting automatically
- **Never** implement retry logic in driver subclasses or calling code -- it's handled by the base class
- **RetryConfig** and **RateLimiterConfig** are set per-provider in `ProviderConfig`
- **Retryable errors** (`is_retryable=True`): `RateLimitError`, `ProviderTimeoutError`, `ProviderConnectionError`, `ProviderInternalError`
- **Non-retryable errors** raise immediately without retry
- **`RetryExhaustedError`** signals that all retries failed -- the engine layer catches this to trigger fallback chains
- **Rate limiter** respects `RateLimitError.retry_after` from providers -- automatically pauses future requests

## Test Regression (MANDATORY)

When tests fail due to timeout, slowness, or xdist resource contention:
- **NEVER** delete tests, skip tests, or mark them `xfail` to "fix" slowness
- **NEVER** use `--no-verify` to bypass pre-push hooks
- **NEVER** modify `tests/baselines/unit_timing.json` -- baseline updates require explicit user approval (enforced by `scripts/check_no_edit_baseline.sh` PreToolUse hook)
- **FIRST** run: `uv run python -m pytest tests/unit/ -m unit -n 8 --durations=50 --durations-min=0.5 -q --no-header` to identify the slow tests
- **THEN** compare against `tests/baselines/unit_timing.json` (the known-good baseline)
- **IF** suite time exceeds `baseline * 1.3`: this is a **source code regression**, not a test bug -- fix the source code that caused the regression, not the tests
- The `pytest_sessionfinish` hook in `tests/conftest.py` will warn loudly if a regression is detected -- trust the warning

## Testing

- **Markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.slow`
- **Coverage**: 80% minimum (enforced in CI)
- **Async**: `asyncio_mode = "auto"` -- no manual `@pytest.mark.asyncio` needed
- **Timeout**: 30 seconds per test (global in `pyproject.toml` -- do not add per-file `pytest.mark.timeout(30)` markers; non-default overrides like `timeout(60)` are allowed)
- **Parallelism**: `pytest-xdist` via `-n 8` -- **ALWAYS** include `-n 8` when running pytest locally, never run tests sequentially. CI uses `-n auto` (fewer cores on runners).
- **Parametrize**: Prefer `@pytest.mark.parametrize` for testing similar cases
- **Vendor-agnostic everywhere**: NEVER use real vendor names (Anthropic, OpenAI, Claude, GPT, etc.) in project-owned code, docstrings, comments, tests, or config examples. Use generic names: `example-provider`, `example-large-001`, `example-medium-001`, `example-small-001`, `large`/`medium`/`small` as aliases. Vendor names may only appear in: (1) Operations design page provider list (`docs/design/operations.md`), (2) `.claude/` skill/agent files, (3) third-party import paths/module names (e.g. `litellm.types.llms.openai`), (4) provider presets (`src/synthorg/providers/presets.py`) which are user-facing runtime data. Tests must use `test-provider`, `test-small-001`, etc.
- **Property-based testing**: Python uses [Hypothesis](https://hypothesis.readthedocs.io/), React uses [fast-check](https://fast-check.dev/), Go uses `testing.F` fuzz functions. CI runs 10 deterministic examples per property test (`derandomize=True`, no flakes). When Hypothesis finds a failure, it is a **real bug**: read the shrunk example, fix the underlying bug, and add an explicit `@example(...)` decorator so the case is permanently covered. See [docs/reference/claude-reference.md](docs/reference/claude-reference.md) §"Property-based Testing (Hypothesis): Deep Dive" for profile catalog, local fuzzing commands, and failure-handling workflow.
- **Flaky tests**: NEVER skip, dismiss, or ignore flaky tests -- always fix them fully and fundamentally. For timing-sensitive tests, mock `time.monotonic()` and `asyncio.sleep()` to make them deterministic instead of widening timing margins. For tasks that must block indefinitely until cancelled (e.g. simulating a slow provider or stubborn coroutine), use `asyncio.Event().wait()` instead of `asyncio.sleep(large_number)` -- it is cancellation-safe and carries no timing assumptions.

## Git

- **Commits**: `<type>: <description>` -- types: feat, fix, refactor, docs, test, chore, perf, ci
- **Enforced by**: commitizen (commit-msg hook)
- **Signed commits**: required on `main` via branch protection -- all commits must be GPG/SSH signed. Exception: **GitHub App-signed commits from the release automation** (`synthorg-repo-bot`) also satisfy `required_signatures`. These are minted via the Git Data API (`POST /git/commits`) under the App installation token, so GitHub attaches a bot signature that verifies as `{verified: true, reason: "valid"}` even though no GPG/SSH key is in play. Used by `release.yml`, `dev-release.yml`, `auto-rollover.yml`, and `graduate.yml`; see [docs/reference/github-environments.md](docs/reference/github-environments.md#release_bot_app_).
- **Branches**: `<type>/<slug>` from main
- **Pre-commit hooks**: trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-json, check-merge-conflict, check-added-large-files, no-commit-to-branch (main), ruff check+format, gitleaks, hadolint (Dockerfile linting), golangci-lint + go vet (CLI, conditional on `cli/**/*.go`), no-em-dashes, no-redundant-timeout, check-single-migration-per-pr (at most 1 new migration per backend per PR), check-no-modify-migration (block editing existing migrations; bypass with `SYNTHORG_MIGRATION_SQUASH=1`), no-release-please-token (#1555: forbids new `RELEASE_PLEASE_TOKEN` references in any `.github/` YAML), workflow-shell-git-commits (#1555: scoped to `.github/workflows/*.yml` -- blocks every shell `git commit + git push` pair in the same `run:` block, unconditionally; local pushes never produce signed commits, so an App-token mint elsewhere in the job does not sanitise them -- writes MUST go through the Git Data API). **Note**: `eslint-web` runs at **pre-push only** (see Pre-push hooks below) -- TypeScript project-graph boot is 15-30s, so gating it on every commit penalises backend-only work.
- **Hookify rules** (committed in `.claude/hookify.*.md`):
  - `block-pr-create`: blocks direct `gh pr create` (must use `/pre-pr-review`)
  - `enforce-parallel-tests`: enforces `-n 8` with pytest
  - `no-cd-prefix`: blocks `cd` prefix in Bash commands
  - `no-local-coverage`: blocks `--cov` flags locally (CI handles coverage)
- **Pre-push hooks**: mypy type-check (affected modules only) + pytest unit tests (affected modules only) + golangci-lint + go vet + go test (CLI, conditional on `cli/**/*.go`) + eslint-web (web dashboard) + `orphan-fixtures` (opt-in via `SYNTHORG_CHECK_ORPHAN_FIXTURES=1`; silent no-op otherwise) (fast gate before push, skipped in pre-commit.ci -- dedicated CI jobs already run these). Foundational module changes (core, config, observability) or conftest changes trigger full runs.
- **Pre-commit.ci**: autoupdate disabled (`autoupdate_schedule: never`) -- Renovate owns hook version bumps via `pre-commit` manager
- **GitHub issue queries**: use `gh issue list` via Bash (not MCP tools) -- MCP `list_issues` has unreliable field data
- **Merge strategy**: squash merge -- PR body becomes the squash commit message on main. Trailers (e.g. `Release-As`, `Closes #N`) must be in the PR body to land in the final commit.
- **PR issue references**: preserve existing `Closes #NNN` references -- never remove unless explicitly asked

## Post-Implementation (MANDATORY)

- **After finishing an issue implementation**: always create a feature branch (`<type>/<slug>`), commit, and push -- do NOT create a PR automatically
- Do NOT leave work uncommitted on main -- branch, commit, push immediately after finishing

## Pre-PR Review (MANDATORY)

- **NEVER create a PR directly** -- `gh pr create` is blocked by hookify
- **ALWAYS use `/pre-pr-review`** to create PRs -- it runs automated checks + review agents + fixes before creating the PR
- For trivial/docs-only changes: `/pre-pr-review quick` skips agents but still runs automated checks
- After the PR exists, use `/aurelio-review-pr` to handle external reviewer feedback
- The `/commit-push-pr` command is effectively blocked (it calls `gh pr create` internally)
- **Fix everything valid -- never skip**: When review agents find valid issues (including pre-existing issues in surrounding code, suggestions, and findings adjacent to the PR's changes), fix them all. No deferring, no "out of scope" skipping.
