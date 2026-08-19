---
description: Run one real objective through the whole orchestration loop as an operator, through the dashboard only, recording every collapse before fixing any of it
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

### No subagents

This skill spawns no Task subagents: the operator drives the run personally,
which is the whole point of it. No subagent-type mapping is needed.

### Browser automation

The skill drives the dashboard through Claude-in-Chrome tools (`ToolSearch`,
`browser_batch`, `find`, `computer`, `get_page_text`, `read_network_requests`).
If OpenCode has no equivalent browser tooling wired, **stop and say so** rather
than substituting `curl` against the API: "everything through the dashboard" is
the contract the run exists to test, and a run that reads its evidence from the
API has proved the backend and not the product.

### Shell compatibility

This runs on Windows with PowerShell. `docker`, `git`, `gh` and `make` behave
the same, but bash-specific syntax needs PowerShell equivalents. The revision
check the skill requires before anything is filed becomes:

```powershell
docker image inspect <backend-image> --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}'
git rev-parse HEAD
```

The inner double quotes are escaped because PowerShell parses the argument
before Docker sees it; the bash form passes them through single quotes intact.
Compare the two outputs yourself: they must be equal, or the running artefact
is not the code under test and the whole run measures something else.

Elsewhere in the skill, translate as needed:

- `VAR=value cmd` becomes `$env:VAR = 'value'; cmd` (PowerShell has no inline
  env-var prefix)
- `cmd 2>/dev/null` becomes `cmd 2>$null`
- `test -f path` becomes `Test-Path path`
- a `<<'EOF'` here-doc becomes a `@'...'@` here-string, with the closing `'@`
  at column 0

---

@.claude/skills/end-to-end-run/SKILL.md

Arguments: $ARGUMENTS
