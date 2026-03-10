# Contributing to AI Company

## Quick Start

```bash
git clone https://github.com/Aureliolo/ai-company.git
cd ai-company
uv sync
```

For the full setup walkthrough (prerequisites, IDE config, etc.), see [docs/getting_started.md](docs/getting_started.md).

## Branching Strategy

Branch from `main`. Use the naming convention:

```text
<type>/<slug>
```

Examples: `feat/agent-engine`, `fix/config-validation`, `docs/api-reference`

Types match commit types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

## Making Changes

1. Create a feature branch: `git checkout -b feat/my-feature main`
2. Install hooks (first time only): `uv run pre-commit install`
3. Make your changes
4. Run quality checks (see below)
5. Commit using conventional commit format
6. Push and open a pull request

## Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/) enforced by commitizen.

```text
<type>: <description>

<optional body>
```

### Types

| Type | When to use |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code restructuring (no behavior change) |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `chore` | Maintenance, tooling, config |
| `perf` | Performance improvement |
| `ci` | CI/CD changes |

### Examples

```text
feat: add YAML config loader with schema validation
fix: prevent division by zero in budget calculator
test: add integration tests for message bus
docs: update API reference for provider layer
```

Keep the description lowercase, imperative, and under 72 characters. The optional body can provide additional context.

## Code Quality Checks

Run these before pushing:

```bash
uv run ruff check src/ tests/              # lint
uv run ruff format --check src/ tests/     # format check
uv run mypy src/                           # type check
```

Auto-fix lint and formatting issues:

```bash
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
```

## Running Tests

```bash
uv run pytest tests/ -m unit               # unit tests (fast)
uv run pytest tests/ -m integration        # integration tests
uv run pytest tests/ -m e2e                # end-to-end tests
uv run pytest tests/ -n auto --cov=ai_company --cov-fail-under=80  # full suite + coverage
```

All tests must pass and coverage must remain at or above 80%.

## Pre-commit Hooks

Hooks run automatically on `git commit`. If a hook fails:

1. Review the output — most hooks (ruff, trailing whitespace) auto-fix the issue
2. Stage the auto-fixed files: `git add .`
3. Commit again

To run all hooks manually:

```bash
uv run pre-commit run --all-files
```

See [docs/getting_started.md](docs/getting_started.md) for the full list of hooks and what each one does.

## Code Style

Code conventions (type hints, docstrings, immutability, line length, etc.) are documented in [CLAUDE.md](CLAUDE.md). Both human contributors and AI assistants follow the same rules.

## Pull Request Process

1. Ensure all quality checks pass locally (lint, type-check, tests)
2. Push your branch: `git push -u origin feat/my-feature`
3. Open a PR against `main`
4. Fill in the PR description: what changed, why, and how to test
5. CI must pass (lint, type-check, test with coverage)
6. Address review feedback
7. Squash-merge when approved

## Project Structure

```text
src/ai_company/       # Main package
  api/  budget/  cli/  communication/  config/  core/
  engine/  memory/  providers/  security/  templates/  tools/
tests/
  unit/  integration/  e2e/
docs/                 # Developer documentation
.github/              # CI, dependabot, actions
```

See [docs/getting_started.md](docs/getting_started.md) for descriptions of each sub-package.

## License

This project is licensed under [BUSL-1.1](LICENSE). By contributing, you agree that your contributions will be licensed under the same terms.
