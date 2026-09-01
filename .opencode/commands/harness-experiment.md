---
description: Run one recursion-depth harness experiment as the experimenter, with the treatment verified on the wire before a single cell is paid for, and its row written to the harness round log when it stops
---

# OpenCode Adapter (read this FIRST, before the skill below)

You are running in **OpenCode**, not Claude Code. Apply these overrides:

### No subagents

This skill spawns no Task subagents: the experimenter drives the sweep
personally and reads its journal back. No subagent-type mapping is needed.

### Shell compatibility

This runs on Windows with PowerShell. `make`, `docker` and `git` behave the
same, but the interpreter path the skill names for mid-sweep work is the
Windows one, `.venv\Scripts\python.exe`, and bash-specific syntax needs
PowerShell equivalents:

- `VAR=value cmd` becomes `$env:VAR = 'value'; cmd` (PowerShell has no inline
  env-var prefix)
- `cmd 2>/dev/null` becomes `cmd 2>$null`
- `test -f path` becomes `Test-Path path`

`make recursion-depth` and `make recursion-depth-record` are unchanged: the
Makefile targets carry the `PYTHONPATH` and the `uv run` themselves, and
`--smoke` / `--record` / `--resume` reach the script as written.

---

@.claude/skills/harness-experiment/SKILL.md

Arguments: $ARGUMENTS
