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

## Install external CLI tools (one-time per machine)

Some gates and the docs build rely on external binaries that are not Python packages: `golangci-lint` (Go linter, used by the CLI), `lychee` (Markdown link-checker), and `d2` (architecture diagram renderer).

Install `golangci-lint` and `lychee` once per machine:

```bash
bash scripts/install_cli_tools.sh
```

The script downloads the pinned `golangci-lint` version that matches CI (`.github/workflows/cli.yml`) and the pinned `lychee` version that matches CI (`.github/workflows/lychee.yml`). Re-run only after bumping a pinned version; subsequent `uv sync` invocations do NOT re-run the script. CI uses its own action-based install steps, so this is strictly a local-developer convenience. The `lychee` binary lands in `~/.local/bin/`; if that directory is not already on `PATH`, the script will print the export line you need to add to `~/.bashrc` / `~/.zshrc`.

To run the link-checker locally:

```bash
uv run pre-commit run lychee --hook-stage pre-push --all-files
```

Install `d2` separately (the docs job pins `v0.7.1`). The fastest path is the upstream installer:

```bash
curl -fsSL https://d2lang.com/install.sh | sh -s -- --version v0.7.1
```

On Windows, install via `winget install Terrastruct.d2` or download the release archive from `https://github.com/terrastruct/d2/releases`. Either way, ensure the resulting `d2` binary is on `PATH`; the docs build invokes it directly.

## Verify Installation

Run the smoke tests to confirm everything is working:

```bash
uv run python -m pytest tests/ -m unit -n 8
```

You should see all tests passing.

## Pre-commit Hooks

Wire the committed Git hooks so code quality checks run automatically on each commit:

```bash
bash scripts/install_git_hooks.sh
```

This points `core.hooksPath` at the version-controlled `scripts/git-hooks/` directory (relative, so every worktree resolves its own copy against its own virtualenv) and installs hooks for the `pre-commit`, `commit-msg`, and `pre-push` stages. Do not run `pre-commit install`: it would write venv-baked wrappers into `.git/hooks/`, which is no longer the hooks path. To run all hooks manually against the entire codebase:

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
| mypy (pre-push) | Type-check affected modules |
| pytest (pre-push) | Run unit tests for affected modules |
| golangci-lint + go vet (pre-push) | Lint/vet Go CLI code (conditional on `cli/**/*.go`) |
| eslint-web (pre-push) | Lint web dashboard (conditional on `web/src/**`) |

## Quality Checks

Run these before pushing to make sure CI will pass:

```bash
# Lint
uv run ruff check .

# Format check (no changes, just verify)
uv run ruff format --check .

# Type check
uv run mypy --num-workers=4 src/ tests/ evals/ docker/ d2_fence.py

# Tests with coverage
uv run python -m pytest tests/ --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80
```

To auto-fix lint issues and reformat:

```bash
uv run ruff check . --fix
uv run ruff format .
```

## Project Layout

```text
synthorg/
  src/synthorg/       # Main package (src layout)
    api/                # Litestar REST + WebSocket routes
    budget/             # Cost tracking and spending controls
    cli/                # Python CLI module (see top-level cli/ for Go CLI)
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
    settings/           # Runtime-editable settings
    backup/             # Backup/restore orchestrator
  tests/
    unit/               # Fast, isolated tests (no I/O)
    integration/        # Tests with I/O, databases, APIs
    e2e/                # Full system tests
  docs/                 # Developer documentation
  docker/               # Dockerfiles, Compose, .env.example
  web/                  # React 19 web dashboard (shadcn/ui + Tailwind CSS)
  .github/              # CI workflows, renovate, actions
  pyproject.toml        # Project config (deps, tools, linters)
  docs/DESIGN_SPEC.md   # Pointer to design specification pages
  CLAUDE.md             # AI assistant quick reference
```

## Web Dashboard Development

The React dashboard lives in `web/`. Prerequisites: **Node.js 22+**.

```bash
npm --prefix web install        # install frontend deps
npm --prefix web run dev         # dev server at http://localhost:5173
npm --prefix web run lint        # ESLint (zero warnings enforced)
npm --prefix web run type-check  # TypeScript type checking
npm --prefix web run test        # Vitest unit tests
npm --prefix web run build       # production build
```

## IDE Setup

### VS Code / Cursor

Recommended extensions:

- **Ruff** (`charliermarsh.ruff`): linting and formatting
- **Pylance** (`ms-python.vscode-pylance`): type checking and IntelliSense

Both Pylance (pyright) and mypy are configured in strict mode. They complement each other: Pylance provides real-time IDE feedback while mypy runs in CI as the authoritative check.

Set the Python interpreter to the project virtual environment:

```text
.venv/Scripts/python    # Windows
.venv/bin/python        # macOS / Linux
```

VS Code should auto-detect the `.venv` directory. If not, use **Python: Select Interpreter** from the command palette.

## Next Steps

- [Contributing Guide](guides/contributing.md): development workflow, testing, and PR process
- [CONTRIBUTING.md](https://github.com/Aureliolo/synthorg/blob/main/.github/CONTRIBUTING.md): branch, commit, and PR workflow
- [CLAUDE.md](https://github.com/Aureliolo/synthorg/blob/main/CLAUDE.md): code conventions and quick command reference
- [Design Specification](design/index.md): full high-level design specification
