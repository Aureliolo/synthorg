# AGENTS.md: SynthOrg (OpenCode)

This project uses a shared configuration for both Claude Code and OpenCode.

**All project conventions, commands, and standards are defined in [CLAUDE.md](CLAUDE.md).**

Read CLAUDE.md for:
- Project structure, package layout, code conventions
- Quick commands (uv, pytest, ruff, mypy, docker, docs)
- Git workflow, commit conventions, branch naming
- Testing standards, coverage requirements
- Design spec (docs/design/): MANDATORY reading before implementation
- Logging, resilience, security patterns
- Convention rollout: every new project-wide convention ships its enforcement gate (the "Convention Rollout (MANDATORY)" section)
- Telemetry (opt-in product telemetry): privacy allowlist + forbidden patterns, environment resolution chain, Docker daemon enrichment

## Memory Directory

When skills or agents reference "the project's auto memory directory", derive the path as:

```
~/.claude/projects/<mangled-cwd>/memory/
```

Where `<mangled-cwd>` is the project root path with path separators replaced by `--` (e.g., `C--Users-Aurelio-synthorg`). The `MEMORY.md` index in that directory is loaded via the global OpenCode config (`~/.config/opencode/opencode.json`).

Files use markdown with YAML frontmatter (`name`, `description`, `type`). Structure:
- **MEMORY.md**: Index with one-line pointers to memory files
- **research-log.md**: One-liner entries from `/research-link`
- **research/**: Detailed research write-ups
- **Individual memory files**: `user_*.md`, `feedback_*.md`, `project_*.md`, `reference_*.md`

## Shell Compatibility

This project runs on Windows. OpenCode uses PowerShell, Claude Code uses bash. Shared skills in `.claude/skills/` should remain shell-neutral or bash-based. PowerShell translation happens in OpenCode adapters (`.opencode/commands/`), not in shared skill content:
- Shared skills: Use POSIX/bash-compatible commands (`ls -la`, `grep`, pipes)
- Git commands work the same in both shells
- OpenCode adapters translate bash to PowerShell equivalents

## OpenCode-Specific Notes

- **Model selection**: Use `/models` to switch between Ollama Cloud models
- **Plan mode**: Toggle with Tab (read-only exploration before execution)
- **Command palette**: Ctrl+P for quick access to commands
- **Session management**: Sessions persist in SQLite, resume with `--continue`
- **Skills**: Loaded from `.claude/skills/` (shared with Claude Code)

## OpenCode Agents

The project defines 22 review agents for automated code review, organized in a 2-tier model routing architecture:
- **Quality tier** (Sonnet-class: ollama-cloud/qwen3-coder-next:cloud): code-reviewer, python-reviewer, frontend-reviewer, go-reviewer, conventions-enforcer, logging-audit, resilience-audit, api-contract-drift
- **Parallel tier** (Haiku-class: ollama-cloud/minimax-m2.5:cloud): async-concurrency-reviewer, comment-analyzer, design-token-audit, docs-consistency, go-conventions-enforcer, go-security-reviewer, infra-reviewer, issue-resolution-verifier, persistence-reviewer, pr-test-analyzer, security-reviewer, silent-failure-hunter, test-quality-reviewer, type-design-analyzer

Agents verify: correctness, security, type design, documentation consistency, API contracts, logging practices, resilience patterns, infrastructure, test quality, and frontend design tokens.

## OpenCode Commands

Custom slash commands for common workflows:
- `/pre-pr-review` - Automated PR creation with checks + review agents + fixes
- `/aurelio-review-pr` - Handle external reviewer feedback on existing PRs
- `/post-merge-cleanup` - Branch cleanup after squash merge
- `/worktree` - Manage parallel git worktrees
- `/codebase-audit` - Deep codebase audit with parallel agents
- `/review-dep-pr` - Review dependency update PRs
- `/research-link` - Research any link/tool/concept
- `/analyse-logs` - Docker container log analysis

## OpenCode Plugins

Plugins extend OpenCode functionality:
- **synthorg-hooks.ts**: PreToolUse/PostToolUse hooks that call the same validation scripts as Claude Code hooks
- **memory.ts**: Memory directory integration with global OpenCode config
