---
description: Watch a PR after creation (CI + external reviewer + security alerts, auto-fix valid feedback, one push per round, until merged/converged)
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

### No subagents

This skill spawns no Task subagents (it is an API-driven watchdog), so no subagent-type mapping is needed.

### GitHub access

The skill reads and writes PR / CI / review / security-alert state via `gh` over shell. In OpenCode this is the native path; MCP GitHub tools may not be configured, so prefer `gh` everywhere the skill mentions a GitHub API call.

### Scheduling

The skill paces its polling with `ScheduleWakeup`. If that tool is unavailable in OpenCode, fall back to the cadence argument by sleeping between ticks (respecting the prompt-cache guidance in the skill) rather than busy-looping.

### Shell compatibility

This runs on Windows with PowerShell. Git / `gh` commands work the same, but shell-specific syntax (pipes, format strings, here-strings) may need PowerShell equivalents. Self-correct when bash syntax fails.

---

@.claude/skills/babysit-pr/SKILL.md

Arguments: $ARGUMENTS
