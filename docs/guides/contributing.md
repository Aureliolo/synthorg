---
title: Contributing
description: Development workflow, testing, code conventions, and pull request process.
---

# Contributing

This guide covers the contributor workflow for SynthOrg, from creating a branch to merging a pull request. For development environment setup (Python, uv, Git hooks), see [Developer Setup](../getting_started.md).

---

## Development Workflow

1. **Create a branch** from `main`:

    ```bash
    git checkout -b feat/my-feature main
    ```

2. **Make changes**: follow the code conventions below.

3. **Run quality checks**: see [Quality Checks](#quality-checks).

4. **Commit** with conventional commit format:

    ```bash
    git commit -m "feat: add new capability"
    ```

5. **Push** and open a PR:

    ```bash
    git push -u origin feat/my-feature
    ```

### Branch Naming

Branches follow the pattern `<type>/<slug>`:

| Type | Purpose |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code refactoring |
| `docs` | Documentation changes |
| `test` | Test additions or fixes |
| `chore` | Maintenance, config, tooling |
| `perf` | Performance improvement |
| `ci` | CI/CD changes |

---

## Commit Conventions

SynthOrg uses [Conventional Commits](https://www.conventionalcommits.org/), enforced by commitizen on the `commit-msg` hook:

```text
<type>: <description>

<optional body>
```

!!! warning "Signed commits required"

    All commits targeting `main` must be GPG or SSH signed (enforced by branch protection). Configure signing before your first PR: see [GitHub's signing commits guide](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits).

**Examples:**

```text
feat: add memory consolidation scheduler
fix: prevent race condition in task assignment
refactor: extract budget enforcer into separate module
docs: add deployment guide
test: add property tests for routing strategy
chore: bump litellm to 1.83.0
```

Breaking changes use `!` after the type:

```text
feat!: replace memory backend protocol with async interface
```

---

## Quality Checks

Run these before pushing to ensure CI will pass:

### Python

```bash
uv run ruff check .                                              # lint
uv run ruff format --check .                                     # format check
uv run mypy --num-workers=4 src/ tests/ evals/ docker/ d2_fence.py  # type check (strict)
```

Auto-fix issues:

```bash
uv run ruff check . --fix                  # auto-fix lint
uv run ruff format .                        # auto-format
```

### Web Dashboard

```bash
npm --prefix web run lint                   # ESLint (zero warnings)
npm --prefix web run type-check             # TypeScript
npm --prefix web run test                   # Vitest unit tests
```

### CLI (Go)

```bash
go -C cli vet ./...                         # go vet
bash -c "cd cli && golangci-lint run"       # linter (first run: bash scripts/install_cli_tools.sh)
go -C cli test ./...                        # tests
```

---

## Testing

SynthOrg uses pytest with three test categories:

```bash
# Unit tests (fast, no I/O)
uv run python -m pytest tests/ -m unit -n 8

# Integration tests (may use I/O, databases)
uv run python -m pytest tests/ -m integration -n 8

# E2E tests (full system)
uv run python -m pytest tests/ -m e2e -n 8

# Full suite with coverage
uv run python -m pytest tests/ --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80
```

### Testing Rules

- **Coverage**: 80% minimum (enforced in CI)
- **Parallelism**: `-n 8 --dist=loadfile` (pytest-xdist) is auto-applied via pyproject `addopts`, so it need not be passed explicitly (including on coverage runs); the flags shown in the example commands above are illustrative
- **Async**: `asyncio_mode = "auto"`, no manual `@pytest.mark.asyncio` needed
- **Timeout**: 30 seconds per test (global default)
- **Markers**: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.slow`
- **Parametrize**: prefer `@pytest.mark.parametrize` for similar cases
- **Property testing**: [Hypothesis](https://hypothesis.readthedocs.io/) with `@given` + `@settings`

---

## Pre-commit Hooks

Hooks run automatically on `git commit` and `git push`. To run all hooks manually:

```bash
uv run pre-commit run --all-files
```

Key hooks include: ruff (lint + format), gitleaks (secret detection), commitizen (commit message format), mypy (affected modules, pre-push), and unit tests (affected modules, pre-push). See [Developer Setup](../getting_started.md#pre-commit-hooks) for the full hook table.

If a hook fails:

1. Review the output: most hooks auto-fix (ruff, trailing whitespace)
2. Stage the auto-fixed files: `git add .`
3. Commit again

---

## Code Style

Key conventions (see [CLAUDE.md](https://github.com/Aureliolo/synthorg/blob/main/CLAUDE.md) for the complete reference):

- **Type hints** on all public functions (mypy strict mode)
- **Docstrings** in Google style on all public classes and functions
- **Immutability**: frozen Pydantic models for config, `model_copy(update=...)` for runtime state
- **Line length**: 88 characters (ruff)
- **Functions**: < 50 lines. **Files**: tiered module-size budget keyed by a `# module-kind:` header (controller 400, service/orchestrator 600, complex_service 1100, repository 500, adapter/integration 700, feature 100, default code 500, tests 800; declarative and generated exempt). See [ADR-0006](https://github.com/Aureliolo/synthorg/blob/main/docs/decisions/0006-tiered-module-size-policy.md).
- **Errors**: handle explicitly, never silently swallow
- **Logging**: use `from synthorg.observability import get_logger` (never `import logging` or `print()`)
- **No `from __future__ import annotations`**: Python 3.14 has PEP 649

---

## Pull Request Process

1. **All quality checks pass** locally (lint, type-check, tests)
2. **Push** your branch: `git push -u origin feat/my-feature`
3. **Open a PR** against `main` with a clear description (what, why, how to test)
4. **CI runs** automatically: lint + type-check + tests with coverage + security scanning
5. **Review**: address feedback, push fixes
6. **Squash-merge** when approved; the PR body becomes the squash commit message

### PR Description Template

```markdown
## Summary

Brief description of what this PR does and why.

## Changes

- Change 1
- Change 2

## Test Plan

- How to test these changes
```

---

## CLA

First-time contributors must sign a Contributor License Agreement (CLA). The CLA bot will comment on your PR with signing instructions. Your signature is recorded in the repository and applies to all future contributions.

The CLA grants a perpetual, non-exclusive license to the project; you retain full ownership of your contributions.

---

## Documentation

### Preview Locally

```bash
uv sync --group docs                      # install docs toolchain (first time)
uv run zensical serve                      # preview at http://127.0.0.1:8000
```

### Writing Conventions

- Use MkDocs Material features: admonitions (`!!! note`), tabs (`=== "Tab"`), code blocks, Mermaid (` ```mermaid `) for flowcharts/sequences/states, D2 (` ```d2 `) for architecture/nested-container diagrams, and Markdown tables for semantically tabular grid/matrix data. Never use ` ```text ` blocks with ASCII/Unicode box-drawing characters for diagrams.
- Building docs locally with D2 diagrams requires the [D2 CLI](https://d2lang.com/tour/install) on `PATH` in addition to `uv sync --group docs`
- YAML frontmatter with `title` and `description`
- Technical but accessible tone
- Cross-reference related pages
- Update `mkdocs.yml` nav when adding new pages

---

## See Also

- [Developer Setup](../getting_started.md): environment setup
- [Fork Setup](fork-setup.md): configure CI on a fresh fork (environments, labels, branch protection, release-bot App)
- [CONTRIBUTING.md](https://github.com/Aureliolo/synthorg/blob/main/.github/CONTRIBUTING.md): formal contributing guidelines
