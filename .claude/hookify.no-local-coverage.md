---
name: no-local-coverage
enabled: true
event: bash
conditions:
  - field: command
    operator: regex_match
    pattern: (?:^|\s)(?:pytest|run\s+pytest|python\s+-m\s+pytest)\b
  - field: command
    operator: contains
    pattern: "--cov"
action: block
---

**Do not run pytest with coverage locally; CI handles it.**

Coverage adds 20-40% overhead. Remove `--cov`, `--cov-report`, and `--cov-fail-under` from your command.

Example: `uv run python -m pytest tests/ -m unit -n 8`
