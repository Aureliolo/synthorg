# Getting Started

Step-by-step guide to set up a development environment for SynthOrg.

## Prerequisites

### Python 3.14+

Download from [python.org](https://www.python.org/downloads/) or use a version manager like pyenv.

### uv (package manager)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Git

Required for cloning the repository and commit hooks. Install from [git-scm.com](https://git-scm.com/).

## Clone and Install

```bash
git clone https://github.com/Aureliolo/synthorg.git
cd synthorg
uv sync
```

`uv sync` creates a virtual environment in `.venv/` and installs all development dependencies (linters, type checker, test runner, pre-commit, etc.).

## Verify Installation

Run the smoke tests to confirm everything is working:

```bash
uv run pytest tests/ -m unit -n auto
```

You should see all tests passing.

## Pre-commit Hooks

Install the Git hooks so code quality checks run automatically on each commit:

```bash
uv run pre-commit install
```

This installs hooks for both `pre-commit` and `commit-msg` stages. To run all hooks manually against the entire codebase:

```bash
uv run pre-commit run --all-files
```

### What the hooks do

| Hook | Purpose |
|------|---------|
| trailing-whitespace | Remove trailing whitespace |
| end-of-file-fixer | Ensure files end with a newline |
| check-yaml / check-toml / check-json | Validate config file syntax |
| check-merge-conflict | Prevent committing merge conflict markers |
| check-added-large-files | Block files > 1 MB |
| no-commit-to-branch | Block direct commits to `main` |
| ruff (check + format) | Lint and format Python code |
| gitleaks | Detect hardcoded secrets |
| commitizen | Enforce conventional commit message format |

## Quality Checks

Run these before pushing to make sure CI will pass:

```bash
# Lint
uv run ruff check src/ tests/

# Format check (no changes, just verify)
uv run ruff format --check src/ tests/

# Type check
uv run mypy src/ tests/

# Tests with coverage
uv run pytest tests/ -n auto --cov=ai_company --cov-fail-under=80
```

To auto-fix lint issues and reformat:

```bash
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
```

## Project Layout

```text
synthorg/
  src/ai_company/       # Main package (src layout)
    api/                # Litestar REST + WebSocket routes
    budget/             # Cost tracking and spending controls
    cli/                # CLI interface (future)
    communication/      # Inter-agent message bus
    config/             # YAML config loading and validation
    core/               # Shared domain models
    engine/             # Agent execution engine
    memory/             # Persistent agent memory
    providers/          # LLM provider abstraction
    security/           # SecOps, approval gates, sandboxing
    templates/          # Pre-built company templates
    tools/              # Tool registry, MCP integration
    hr/                 # HR engine (hiring, firing, performance)
    observability/      # Structured logging, correlation tracking
    persistence/        # Pluggable persistence backends
  tests/
    unit/               # Fast, isolated tests (no I/O)
    integration/        # Tests with I/O, databases, APIs
    e2e/                # Full system tests
  docs/                 # Developer documentation
  docker/               # Dockerfiles, Compose, .env.example
  web/                  # Web UI scaffold (nginx + placeholder)
  .github/              # CI workflows, dependabot, actions
  pyproject.toml        # Project config (deps, tools, linters)
  DESIGN_SPEC.md        # Full high-level design specification
  CLAUDE.md             # AI assistant quick reference
```

## IDE Setup

### VS Code / Cursor

Recommended extensions:

- **Ruff** (`charliermarsh.ruff`) — linting and formatting
- **Pylance** (`ms-python.vscode-pylance`) — type checking and IntelliSense

Both Pylance (pyright) and mypy are configured in strict mode. They complement each other: Pylance provides real-time IDE feedback while mypy runs in CI as the authoritative check.

Set the Python interpreter to the project virtual environment:

```text
.venv/Scripts/python    # Windows
.venv/bin/python        # macOS / Linux
```

VS Code should auto-detect the `.venv` directory. If not, use **Python: Select Interpreter** from the command palette.

## Next Steps

- [CONTRIBUTING.md](https://github.com/Aureliolo/synthorg/blob/main/.github/CONTRIBUTING.md) — branch, commit, and PR workflow
- [CLAUDE.md](https://github.com/Aureliolo/synthorg/blob/main/CLAUDE.md) — code conventions and quick command reference
- [Design Specification](design_spec.md) — full high-level design specification
