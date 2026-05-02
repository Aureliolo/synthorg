---
name: enforce-parallel-tests
enabled: true
event: bash
conditions:
  - field: command
    operator: regex_match
    pattern: (?:^|\s)(?:pytest|run\s+pytest|python\s+-m\s+pytest)\b
  - field: command
    operator: not_contains
    pattern: "-n 8"
action: block
---

**Always use `-n 8` with pytest for parallel execution.**

Add `-n 8` to your pytest command. Never run tests sequentially or with `-n auto` (32 workers causes crashes and is slower due to contention).

Example: `uv run python -m pytest tests/ -m unit -n 8`

<!-- Pattern matches both `uv run pytest` and `uv run python -m pytest`
     (the canonical form per CLAUDE.md). Does not trigger on git commits
     or other commands that mention "pytest" in strings/messages. -->
