---
description: Pre-PR review pipeline (checks + review agents + fixes + create PR)
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

### Subagent type mapping

The skill below references `subagent_type` values from Claude Code plugins. In OpenCode, use the corresponding agent definitions from `.opencode/agents/` instead. When spawning a subagent, load the agent's `.md` file as the subagent prompt and use the model specified in its frontmatter.

| Skill references this `subagent_type` | Use this OpenCode agent instead |
|---|---|
| `pr-review-toolkit:code-reviewer` | `.opencode/agents/code-reviewer.md` |
| `python-reviewer` | `.opencode/agents/python-reviewer.md` |
| `pr-review-toolkit:pr-test-analyzer` | `.opencode/agents/pr-test-analyzer.md` |
| `pr-review-toolkit:silent-failure-hunter` | `.opencode/agents/silent-failure-hunter.md` |
| `pr-review-toolkit:comment-analyzer` | `.opencode/agents/comment-analyzer.md` |
| `pr-review-toolkit:type-design-analyzer` | `.opencode/agents/type-design-analyzer.md` |
| `security-reviewer` | `.opencode/agents/security-reviewer.md` |
| `persistence-reviewer` | `.opencode/agents/persistence-reviewer.md` |
| `go-reviewer` | `.opencode/agents/go-reviewer.md` |
| `pr-review-toolkit:code-simplifier` | `.opencode/agents/code-reviewer.md` (use code-reviewer with simplification focus) |
| `.claude/agents/design-token-audit.md` | `.opencode/agents/design-token-audit.md` |
| `.claude/agents/tool-parity-checker.md` | `.opencode/agents/tool-parity-checker.md` |

Custom prompts defined inline in the skill (logging-audit, resilience-audit, conventions-enforcer, frontend-reviewer, api-contract-drift, infra-reviewer, test-quality-reviewer, async-concurrency-reviewer, go-conventions-enforcer, docs-consistency, issue-resolution-verifier) should use the matching `.opencode/agents/<name>.md` as the base agent, then append the custom prompt from the skill.

### PR creation

The skill uses `mcp__github__create_pull_request`. In OpenCode, use `gh pr create` via shell instead (MCP GitHub tools may not be configured).

### Shell compatibility

This runs on Windows with PowerShell. Git commands work the same, but shell-specific syntax (pipes, format strings) may need PowerShell equivalents. Self-correct when bash syntax fails.

---

@.claude/skills/pre-pr-review/SKILL.md

Arguments: $ARGUMENTS
