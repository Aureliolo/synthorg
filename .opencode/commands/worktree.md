---
description: Manage parallel worktrees (setup, cleanup, status, tree, rebase)
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

### Binary name

Wherever the skill says `claude` as a command to run, use `opencode-cli` instead:
- `cd <path> && claude` becomes `opencode-cli --cwd <path>`
- "Claude Code prompts" means "OpenCode prompts"
- "Claude Code instances" means "OpenCode instances"

### Config file copying

When the skill copies `.claude/` local files to worktrees, ALSO copy OpenCode config:
- Copy `opencode.json` from project root to the worktree root
- Copy `.opencode/` directory to the worktree (commands, agents, plugins)
- The `.claude/` files should still be copied too (shared source of truth)

### Prompt generation

When generating prompts for each worktree (step 5), generate prompts that work in OpenCode:
- Reference CLAUDE.md (OpenCode reads it via instructions)
- Reference AGENTS.md (OpenCode reads it natively)
- The prompt content itself is the same; it's project instructions, not tool-specific

### Shell compatibility

This runs on Windows with PowerShell. Git commands work the same, but bash-specific syntax (for loops, test -f, etc.) needs PowerShell equivalents:
- `test -f file` becomes `Test-Path file`
- `for f in .claude/*.local.*; do ... done` becomes `Get-ChildItem .claude/*.local.* | ForEach-Object { ... }`
- `cp` works in PowerShell (alias for Copy-Item)

---

@.claude/skills/worktree/SKILL.md

Arguments: $ARGUMENTS
