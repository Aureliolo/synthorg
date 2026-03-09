# CLAUDE.md — AI Company

## Project

- **What**: Framework for orchestrating autonomous AI agents within a virtual company structure
- **Python**: 3.14+ (PEP 649 native lazy annotations)
- **License**: BUSL-1.1 (converts to Apache 2.0 on 2030-02-27)
- **Layout**: `src/ai_company/` (src layout), `tests/` (unit/integration/e2e)
- **Design**: [DESIGN_SPEC.md](DESIGN_SPEC.md) (full high-level spec)

## Design Spec (MANDATORY)

- **ALWAYS read `DESIGN_SPEC.md`** before implementing any feature or planning any issue
- The design spec is the **starting point** for architecture, data models, and behavior
- If implementation deviates from the spec (better approach found, scope evolved, etc.), **alert the user and explain why** — user decides whether to proceed or update the spec
- Do NOT silently diverge — every deviation needs explicit user approval
- When a spec section is referenced (e.g. "Section 10.2"), read that section verbatim before coding
- When approved deviations occur, update `DESIGN_SPEC.md` to reflect the new reality

## Planning (MANDATORY)

- Every implementation plan must be **presented to the user** for accept/deny before coding starts
- At **every phase** of planning and implementation, be critical — actively look for ways to improve the design in the spirit of what we're building (robustness, correctness, simplicity, future-proofing where it's free)
- Surface improvements as suggestions, not silent changes — user decides
- **Prioritize issues by dependency order**, not priority labels — unblocked dependencies come first

## Quick Commands

```bash
uv sync                                    # install all deps (dev + test)
uv run ruff check src/ tests/              # lint
uv run ruff check src/ tests/ --fix        # lint + auto-fix
uv run ruff format src/ tests/             # format
uv run mypy src/ tests/                    # type-check (strict)
uv run pytest tests/ -m unit               # unit tests only
uv run pytest tests/ -m integration        # integration tests only
uv run pytest tests/ -m e2e                # e2e tests only
uv run pytest tests/ -n auto --cov=ai_company --cov-fail-under=80  # full suite + coverage
uv run pre-commit run --all-files          # all pre-commit hooks
```

## Package Structure

```text
src/ai_company/
  api/            # FastAPI REST + WebSocket routes
  budget/         # Cost tracking, budget enforcement (pre-flight/in-flight checks, auto-downgrade), billing periods
  cli/            # Typer CLI commands
  communication/  # Message bus, dispatcher, messenger, channels, delegation, loop prevention, conflict resolution, meeting protocol
  config/         # YAML company config loading and validation
  core/           # Shared domain models and base classes
  engine/         # Agent orchestration, execution loops, parallel execution, task decomposition, routing, task assignment, task lifecycle, recovery, shutdown, workspace isolation, and coordination error classification
  memory/         # Persistent agent memory (Mem0 initial, custom stack future — ADR-001)
  persistence/    # Operational data persistence — pluggable PersistenceBackend protocol, SQLite initial (§7.6)
  observability/  # Structured logging, correlation tracking, log sinks
  providers/      # LLM provider abstraction (LiteLLM adapter)
  security/       # SecOps agent, approval gates, audit
  templates/      # Pre-built company templates, personality presets, and builder
  tools/          # Tool registry, built-in tools (file_system/, git, sandbox/), MCP integration, role-based access
```

## Shell Usage

- **NEVER use `cd` in Bash commands** — the working directory is already set to the project root. Use absolute paths or run commands directly. Do NOT prefix commands with `cd C:/Users/Aurelio/ai-company &&`.

## Code Conventions

- **No `from __future__ import annotations`** — Python 3.14 has PEP 649
- **PEP 758 except syntax**: use `except A, B:` (no parentheses) — ruff enforces this on Python 3.14
- **Type hints**: all public functions, mypy strict mode
- **Docstrings**: Google style, required on public classes/functions (enforced by ruff D rules)
- **Immutability**: create new objects, never mutate existing ones. For non-Pydantic internal collections (registries, `BaseTool`), use `copy.deepcopy()` at construction + `MappingProxyType` wrapping for read-only enforcement. For `dict`/`list` fields in frozen Pydantic models, rely on `frozen=True` for field reassignment prevention and `copy.deepcopy()` at system boundaries (tool execution, LLM provider serialization, inter-agent delegation, serializing for persistence).
- **Config vs runtime state**: frozen Pydantic models for config/identity; separate mutable-via-copy models (using `model_copy(update=...)`) for runtime state that evolves (e.g. agent execution state, task progress). Never mix static config fields with mutable runtime fields in one model.
- **Models**: Pydantic v2 (`BaseModel`, `model_validator`, `computed_field`, `ConfigDict`). Adopted conventions: use `@computed_field` for derived values instead of storing + validating redundant fields (e.g. `TokenUsage.total_tokens`); use `NotBlankStr` (from `core.types`) for all identifier/name fields — including optional (`NotBlankStr | None`) and tuple (`tuple[NotBlankStr, ...]`) variants — instead of manual whitespace validators.
- **Async concurrency**: prefer `asyncio.TaskGroup` for fan-out/fan-in parallel operations in new code (e.g. multiple tool invocations, parallel agent calls). Prefer structured concurrency over bare `create_task`. Existing code is being migrated incrementally.
- **Line length**: 88 characters (ruff)
- **Functions**: < 50 lines, files < 800 lines
- **Errors**: handle explicitly, never silently swallow
- **Validate**: at system boundaries (user input, external APIs, config files)

## Logging

- **Every module** with business logic MUST have: `from ai_company.observability import get_logger` then `logger = get_logger(__name__)`
- **Never** use `import logging` / `logging.getLogger()` / `print()` in application code
- **Variable name**: always `logger` (not `_logger`, not `log`)
- **Event names**: always use constants from the domain-specific module under `ai_company.observability.events` (e.g. `PROVIDER_CALL_START` from `events.provider`, `BUDGET_RECORD_ADDED` from `events.budget`, `CONFLICT_DETECTED` from `events.conflict`, `MEETING_STARTED` from `events.meeting`, `CLASSIFICATION_START` from `events.classification`). Import directly: `from ai_company.observability.events.<domain> import EVENT_CONSTANT`
- **Structured kwargs**: always `logger.info(EVENT, key=value)` — never `logger.info("msg %s", val)`
- **All error paths** must log at WARNING or ERROR with context before raising
- **All state transitions** must log at INFO
- **DEBUG** for object creation, internal flow, entry/exit of key functions
- Pure data models, enums, and re-exports do NOT need logging

## Resilience

- **All provider calls** go through `BaseCompletionProvider` which applies retry + rate limiting automatically
- **Never** implement retry logic in driver subclasses or calling code — it's handled by the base class
- **RetryConfig** and **RateLimiterConfig** are set per-provider in `ProviderConfig`
- **Retryable errors** (`is_retryable=True`): `RateLimitError`, `ProviderTimeoutError`, `ProviderConnectionError`, `ProviderInternalError`
- **Non-retryable errors** raise immediately without retry
- **`RetryExhaustedError`** signals that all retries failed — the engine layer catches this to trigger fallback chains
- **Rate limiter** respects `RateLimitError.retry_after` from providers — automatically pauses future requests

## Testing

- **Markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.slow`
- **Coverage**: 80% minimum (enforced in CI)
- **Async**: `asyncio_mode = "auto"` — no manual `@pytest.mark.asyncio` needed
- **Timeout**: 30 seconds per test
- **Parallelism**: `pytest-xdist` via `-n auto`
- **Parametrize**: Prefer `@pytest.mark.parametrize` for testing similar cases
- **Vendor-agnostic everywhere**: NEVER use real vendor names (Anthropic, OpenAI, Claude, GPT, etc.) in project-owned code, docstrings, comments, tests, or config examples. Use generic names: `example-provider`, `example-large-001`, `example-medium-001`, `example-small-001`, `large`/`medium`/`small` as aliases. Vendor names may only appear in: (1) DESIGN_SPEC.md provider list (listing supported providers), (2) `.claude/` skill/agent files, (3) third-party import paths/module names (e.g. `litellm.types.llms.openai`). Tests must use `test-provider`, `test-small-001`, etc.

## Git

- **Commits**: `<type>: <description>` — types: feat, fix, refactor, docs, test, chore, perf, ci
- **Enforced by**: commitizen (commit-msg hook)
- **Branches**: `<type>/<slug>` from main
- **Pre-commit hooks**: trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-json, check-merge-conflict, check-added-large-files, no-commit-to-branch (main), ruff check+format, gitleaks
- **GitHub issue queries**: use `gh issue list` via Bash (not MCP tools) — MCP `list_issues` returns `null` for milestone data
- **PR issue references**: preserve existing `Closes #NNN` references — never remove unless explicitly asked

## Post-Implementation (MANDATORY)

- **After finishing an issue implementation**: always create a feature branch (`<type>/<slug>`), commit, and push — do NOT create a PR automatically
- Do NOT leave work uncommitted on main — branch, commit, push immediately after finishing

## Pre-PR Review (MANDATORY)

- **NEVER create a PR directly** — `gh pr create` is blocked by hookify
- **ALWAYS use `/pre-pr-review`** to create PRs — it runs automated checks + review agents + fixes before creating the PR
- For trivial/docs-only changes: `/pre-pr-review quick` skips agents but still runs automated checks
- After the PR exists, use `/aurelio-review-pr` to handle external reviewer feedback
- The `/commit-push-pr` command is effectively blocked (it calls `gh pr create` internally)
- **Fix everything valid — never skip**: When review agents find valid issues (including pre-existing issues in surrounding code, suggestions, and findings adjacent to the PR's changes), fix them all. No deferring, no "out of scope" skipping.

## CI

- **Jobs**: lint (ruff) + type-check (mypy src/ tests/) + test (pytest + coverage) run in parallel → ci-pass (gate)
- **Matrix**: Python 3.14
- **Dependabot**: daily uv + github-actions updates, grouped minor/patch, no auto-merge
- **Secret scanning**: gitleaks workflow on push/PR + weekly schedule
- **Dependency review**: license allow-list (permissive only), PR comment summaries
- **Coverage**: Codecov integration (replaces artifact-only uploads)

## Dependencies

- **Pinned**: all versions use `==` in `pyproject.toml`
- **Groups**: `test` (pytest + plugins), `dev` (includes test + ruff, mypy, pre-commit, commitizen)
- **Install**: `uv sync` installs everything (dev group is default)
