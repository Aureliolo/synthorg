---
description: "Full codebase audit: launches 155 specialized agents to find issues across Python/React/Go/docs/website, writes findings to _audit/latest/findings/, then triages with user"
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

### Subagent spawning

The rewritten skill launches 155 audit agents with custom embedded prompts via the `Agent` tool. These are NOT mapped to `.opencode/agents/`; they use inline prompts defined in the skill itself. Spawn each agent with its prompt from the skill's Agent Roster section.

### Scope changes

Supported scopes: `full`, `src/`, `web/`, `cli/`, `docs/`. The old scopes `.github/`, `ci`, `docker/`, `site/`, `src/synthorg/` are no longer valid.

### GitHub issue creation

The skill uses `mcp__github__issue_write`. In OpenCode, use `gh issue create` via shell instead.

### Shell compatibility

This runs on Windows with PowerShell. Self-correct when bash syntax fails.

The new run-history layout (`_audit/runs/<run-id>/` plus `_audit/latest` link) needs PowerShell substitutes for the bash setup commands. Use a timestamped run-id (`Get-Date -Format 'yyyy-MM-dd-HHmmss'`) so back-to-back runs do not collide:

```powershell
$runId = Get-Date -Format 'yyyy-MM-dd-HHmmss'
$runDir = "_audit/runs/$runId"
New-Item -ItemType Directory -Force -Path "$runDir/findings" | Out-Null

# Always remove any existing _audit/latest first so repointing is idempotent.
# New-Item -Force does not reliably replace an existing symlink/junction with a
# different target across PowerShell versions, so do it explicitly.
if (Test-Path "_audit/latest") {
    Remove-Item -Force -Recurse "_audit/latest"
}

# Try symlink first (requires Developer Mode or admin), fall back to junction
# (no privileges needed) so `_audit/latest/findings/...` writes resolve to the
# current run dir either way.
try {
    New-Item -ItemType SymbolicLink -Path "_audit/latest" -Target "runs/$runId" -ErrorAction Stop | Out-Null
} catch {
    New-Item -ItemType Junction -Path "_audit/latest" -Target "runs/$runId" | Out-Null
}
```

Both SymbolicLink and Junction make `_audit/latest` resolve as a directory, so downstream writes to `_audit/latest/findings/<file>` succeed without changes to the rest of the skill.

---

@.claude/skills/codebase-audit/SKILL.md

Arguments: $ARGUMENTS
