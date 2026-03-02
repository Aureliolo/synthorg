# CLAUDE.md — AI Company

## Project

- **What**: Framework for orchestrating autonomous AI agents within a virtual company structure
- **Python**: 3.14+ (PEP 649 native lazy annotations)
- **License**: BUSL-1.1 (converts to Apache 2.0 on 2030-02-27)
- **Layout**: `src/ai_company/` (src layout), `tests/` (unit/integration/e2e)
- **Design**: [DESIGN_SPEC.md](DESIGN_SPEC.md) (full high-level spec)

## Quick Commands

```bash
uv sync                                    # install all deps (dev + test)
uv run ruff check src/ tests/              # lint
uv run ruff check src/ tests/ --fix        # lint + auto-fix
uv run ruff format src/ tests/             # format
uv run mypy src/ tests/                    # type-check (strict)
uv run pytest tests/ -m unit               # unit tests only
uv run pytest tests/ -m integration        # integration tests only
uv run pytest tests/ -n auto --cov=ai_company --cov-fail-under=80  # full suite + coverage
uv run pre-commit run --all-files          # all pre-commit hooks
```

## Package Structure

```text
src/ai_company/
  api/            # FastAPI REST + WebSocket routes
  budget/         # Per-agent cost tracking and spending controls
  cli/            # Typer CLI commands
  communication/  # Inter-agent message bus and channels
  config/         # YAML company config loading and validation
  core/           # Shared domain models and base classes
  engine/         # Agent execution engine and task lifecycle
  memory/         # Persistent agent memory (Mem0 adapter)
  observability/  # Structured logging, correlation tracking, log sinks
  providers/      # LLM provider abstraction (LiteLLM adapter)
  security/       # SecOps agent, approval gates, sandboxing
  templates/      # Pre-built company templates and builder
  tools/          # Tool registry, MCP integration, role-based access
```

## Shell Usage

- **NEVER use `cd` in Bash commands** — the working directory is already set to the project root. Use absolute paths or run commands directly. Do NOT prefix commands with `cd C:/Users/Aurelio/ai-company &&`.

## Code Conventions

- **No `from __future__ import annotations`** — Python 3.14 has PEP 649
- **PEP 758 except syntax**: use `except A, B:` (no parentheses) — ruff enforces this on Python 3.14
- **Type hints**: all public functions, mypy strict mode
- **Docstrings**: Google style, required on public classes/functions (enforced by ruff D rules)
- **Immutability**: create new objects, never mutate existing ones
- **Models**: Pydantic v2 (`BaseModel`, `model_validator`, `ConfigDict`)
- **Line length**: 88 characters (ruff)
- **Functions**: < 50 lines, files < 800 lines
- **Errors**: handle explicitly, never silently swallow
- **Validate**: at system boundaries (user input, external APIs, config files)

## Logging

- **Every module** with business logic MUST have: `from ai_company.observability import get_logger` then `logger = get_logger(__name__)`
- **Never** use `import logging` / `logging.getLogger()` / `print()` in application code
- **Variable name**: always `logger` (not `_logger`, not `log`)
- **Event names**: always use constants from `ai_company.observability.events`
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
- **Vendor-agnostic fixtures**: use fake model IDs/names in tests (e.g. `test-haiku-001`, `test-provider`), never real vendor model IDs — tests must not be coupled to external providers

## Git

- **Commits**: `<type>: <description>` — types: feat, fix, refactor, docs, test, chore, perf, ci
- **Enforced by**: commitizen (commit-msg hook)
- **Branches**: `<type>/<slug>` from main
- **Pre-commit hooks**: trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-json, check-merge-conflict, check-added-large-files, no-commit-to-branch (main), ruff check+format, gitleaks

## CI

- **Jobs**: lint (ruff) + type-check (mypy src/ tests/) + test (pytest + coverage) run in parallel → ci-pass (gate)
- **Matrix**: Python 3.14
- **Dependabot**: daily uv + github-actions updates, grouped minor/patch, no auto-merge
- **Secret scanning**: gitleaks workflow on push/PR + weekly schedule
- **Dependency review**: license allow-list (permissive only), PR comment summaries
- **Coverage**: Codecov integration (replaces artifact-only uploads)

## Dependencies

- **Pinned**: all versions use `==` in `pyproject.toml`
- **Groups**: `test` (pytest + plugins), `dev` (includes test + ruff, mypy, pre-commit, commitizen, pydantic)
- **Install**: `uv sync` installs everything (dev group is default)
