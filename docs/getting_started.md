# Getting Started

Set up a development environment for SynthOrg.

SynthOrg takes a description of a piece of software and splits the work into a tree of
parts, builds the parts in parallel, each in its own workspace, and has each one checked by
something that did not write it, on your hardware against models you choose. This page is about the repository and the toolchain, not about running the platform.
To run it, see the [User Guide](user_guide.md).

!!! warning "Pre-alpha"

    SynthOrg is pre-alpha. The loop has been driven live against a real deployment twelve
    times and has never reached the assembly stage: no run has produced an assembled
    deliverable. Set this up to research and contribute, not to get software built.

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

Some gates and the docs build rely on external binaries that are not Python packages: `golangci-lint` (Go linter, used by the CLI), `lychee` (Markdown link-checker), `vale` (prose linter) and `d2` (architecture diagram renderer).

Install the first three once per machine:

```bash
bash scripts/install_cli_tools.sh
```

Pass a single tool name (`lychee`, `golangci-lint`, `vale`) to install just that one. The script downloads the pinned `golangci-lint` version that matches CI (`.github/workflows/verify-cli.yml`) and the pinned `lychee` version that matches CI (`.github/workflows/verify-links.yml`), and runs `vale sync` after installing vale. Re-run only after bumping a pinned version; subsequent `uv sync` invocations do NOT re-run the script. CI uses its own action-based install steps, so this is strictly a local-developer convenience. The `lychee` binary lands in `~/.local/bin/`; if that directory is not already on `PATH`, the script will print the export line you need to add to `~/.bashrc` / `~/.zshrc`.

To run the link-checker locally:

```bash
uv run pre-commit run lychee --hook-stage pre-push --all-files
```

Install `d2` separately (the docs job pins `v0.8.2`). The quickest path is the upstream installer:

```bash
curl -fsSL https://d2lang.com/install.sh | sh -s -- --version v0.8.2
```

On Windows, install via `winget install Terrastruct.d2` or download the release archive from `https://github.com/terrastruct/d2/releases`. Either way, ensure the resulting `d2` binary is on `PATH`; the docs build invokes it directly.

## Verify Installation

Run the unit tier to confirm everything is working:

```bash
uv run python -m pytest tests/ -m unit
```

Parallelism (`-n 8 --dist=loadfile`) is applied automatically from `pyproject.toml`, so there is no flag to pass. You should see all tests passing.

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
| check-added-large-files | Block files over 1 MB (`--maxkb=1024`) |
| no-commit-to-branch | Block direct commits to `main` |
| ruff (check + format) | Lint and format Python code |
| gitleaks | Detect hardcoded secrets |
| commitizen | Enforce conventional commit message format |
| consolidated-python-gates | Run the repository's convention gates in one bounded pool |
| vale (pre-push) | Prose linter over the Markdown you changed |
| mypy (pre-push) | Type-check affected modules |
| pytest (pre-push) | Run unit tests for affected modules |
| golangci-lint + go vet + go test (pre-push) | Lint, vet and test each Go module, scoped to its own tree (conditional on `cli/**/*.go` and `docker/sidecar/**/*.go`) |
| web-checks (pre-push) | ESLint the pushed dashboard files, plus knip and circular-import scans, run concurrently (conditional on `web/**`) |
| python-audits (pre-push) | Dead code, docstring coverage and dependency hygiene, run concurrently |

## Quality Checks

Run these before pushing to make sure CI will pass:

```bash
# Lint
uv run ruff check .

# Format check (no changes, just verify)
uv run ruff format --check .

# Type check (uses the mypy daemon; seconds once warm)
make typecheck

# Tests with coverage
uv run python -m pytest tests/ --ignore=tests/benchmarks/ --cov=synthorg --cov-fail-under=80
```

To auto-fix lint issues and reformat:

```bash
uv run ruff check . --fix
uv run ruff format .
```

The integration, e2e and conformance tiers provision real services and are run by CI on the
pushed branch rather than locally.

## Project Layout

```text
synthorg/
  src/synthorg/       # Main package (src layout)
    api/                # Litestar REST + WebSocket routes
    backup/             # Backup/restore orchestrator
    budget/             # Cost tracking and spending controls
    cli/                # Python CLI module (see top-level cli/ for Go CLI)
    communication/      # Inter-agent message bus
    config/             # YAML config loading and validation
    coordination/       # Multi-agent coordination service and state
    core/               # Shared domain models
    engine/             # Decomposition, dispatch, execution, review
    hr/                 # Roster: hiring, firing, role staffing, performance
    knowledge/          # Ingested external corpus and provenance
    llm/                # Prompt-purpose registry, model-pin metadata
    memory/             # Persistent agent memory
    observability/      # Structured logging, correlation tracking
    persistence/        # Pluggable persistence backends
    providers/          # LLM provider abstraction
    security/           # Approval gates, rule engine, sandboxing
    settings/           # Runtime-editable settings
    templates/          # Pre-built company templates
    tools/              # Tool registry, MCP integration
    workers/            # Distributed task-queue workers
  tests/
    unit/               # Fast, isolated tests (no I/O)
    integration/        # Tests with I/O, databases, APIs
    conformance/        # Dual-backend (SQLite + Postgres) parity suite
    e2e/                # Full system tests
  evals/                # Golden-company benchmark (out-of-package)
  docs/                 # Developer documentation
  docker/               # Dockerfiles, Compose, .env.example
  web/                  # React 19 web dashboard (shadcn/ui + Tailwind CSS)
  cli/                  # Go CLI (Docker orchestrator)
  .github/              # CI workflows, renovate, actions
  pyproject.toml        # Project config (deps, tools, linters)
  docs/DESIGN_SPEC.md   # Pointer to design specification pages
  CLAUDE.md             # AI assistant quick reference
```

## Web Dashboard Development

The React dashboard lives in `web/`. Prerequisites: **Node.js 24** (CI pins `24.20.0`).

```bash
npm --prefix web install        # install frontend deps
npm --prefix web run dev         # dev server at http://localhost:5173
npm --prefix web run lint        # ESLint (zero warnings enforced)
npm --prefix web run type-check  # TypeScript type checking
npm --prefix web run test        # Vitest unit tests
npm --prefix web run build       # production build
```

The dashboard is a pure API consumer: it persists no application state client-side, so every
feature it offers is driven through the API, with a WebSocket delivering live updates on top.

## IDE Setup

### VS Code / Cursor

Recommended extensions:

- **Ruff** (`charliermarsh.ruff`): linting and formatting
- **Pylance** (`ms-python.vscode-pylance`): type checking and IntelliSense

Both Pylance (pyright) and mypy are configured in strict mode. They complement each other: Pylance provides real-time IDE feedback while mypy is the authoritative check, enforced on every push. Pyright also runs in CI as a second opinion, gated on a shrink-only per-rule baseline (`scripts/pyright_finding_baseline.json`) because its narrowing and overload analysis disagrees with mypy in places.

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
