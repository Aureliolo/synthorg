---
description: Plan max-safe parallel worktrees (survey open issues, map inflight, propose conflict-free set)
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

## Subagent type mapping

The skill spawns sub-agents via the `Task` tool with Claude Code `subagent_type` values. In OpenCode, load the corresponding `.opencode/agents/` definition as the subagent prompt and use the model in its frontmatter:

| Skill references this `subagent_type` | Use this OpenCode agent instead |
|---|---|
| `issue-resolution-verifier` (epic-validation grading, Step 6) | `.opencode/agents/issue-resolution-verifier.md` |

For the footprint-estimation sub-agents (Step 3), there is no dedicated OpenCode agent: run them inline with a general-purpose subagent and a `haiku`-tier model (mechanical file counts).

## GitHub queries

The skill uses `gh` via the shell (`gh issue list`, `gh issue view`, `gh pr list`). This is correct in OpenCode too: `gh` is a plain shell command, not an MCP tool. Do NOT substitute an MCP `list_issues` call.

## Hand-off to /worktree setup

Step 7 hands the chosen groupings to `/worktree setup`. In OpenCode that is the `worktree` command (`.opencode/commands/worktree.md`); invoke it with the same one-line-per-worktree groupings (`<branch> #issues "Description"`). Do not re-implement worktree creation here.

## Shell compatibility

This runs on Windows with PowerShell. The skill's `git` / `gh` invocations work the same, but bash-specific syntax (`for ... do ... done`, `test -f`, `[ -n "$x" ]`, `$(...)` substitution in conditionals) needs PowerShell equivalents:
- `test -f file` becomes `Test-Path file`
- `for f in ...; do ... done` becomes `Get-ChildItem ... | ForEach-Object { ... }`
- The `gh ... --jq '...'` filters work as-is (jq runs inside `gh`).

---

@.claude/skills/plan-work/SKILL.md

Arguments: $ARGUMENTS
