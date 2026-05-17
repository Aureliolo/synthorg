---
name: conventions-enforcer
description: "Enforces SynthOrg-specific Python conventions beyond standard style: immutability patterns, vendor-name policy, Python 3.14 / PEP 758, Pydantic configs, code structure limits, observability-logger imports, and error-handling discipline. Use for changes under src/synthorg/ and tests/."
model: sonnet
color: blue
tools: Read, Grep, Glob
---

# Conventions Enforcer Agent

You enforce SynthOrg-specific coding conventions that go beyond standard Python style guides.

## What to Check

### 1. Immutability (HIGH)

- Pydantic models missing `frozen=True` in `ConfigDict` (config/identity models)
- Mutable runtime state not using `model_copy(update=...)` pattern
- Missing `copy.deepcopy()` at system boundaries (tool execution, LLM provider serialization, inter-agent delegation, persistence)
- Missing `MappingProxyType` wrapping for non-Pydantic internal collections
- Mixing static config fields with mutable runtime fields in one model

### 2. Vendor Names (HIGH)

See `.claude/skills/aurelio-review-pr/SKILL.md` for the canonical policy. In summary:
- Real vendor names (Anthropic, OpenAI, Claude, GPT, Gemini, etc.) are FORBIDDEN in project code, docstrings, comments, tests, or config examples
- Allowed only in: `docs/design/operations.md`, `.claude/` files, third-party import paths, `providers/presets.py`
- Tests must use `test-provider`, `test-small-001`, etc. (canonical test names)

### 3. Python 3.14 Conventions

- `from __future__ import annotations`: forbidden, Python 3.14 has PEP 649 native lazy annotations (CRITICAL)
- `except (A, B):` with parentheses instead of PEP 758 `except A, B:`; ruff enforces this on Python 3.14 (MAJOR)

### 4. Pydantic Patterns (HIGH)

- Missing `allow_inf_nan=False` in `ConfigDict` declarations
- Storing redundant computed values instead of using `@computed_field`
- Using plain `str` for identifier/name fields instead of `NotBlankStr`
- Optional identifiers not using `NotBlankStr | None`

### 5. Code Structure (MEDIUM)

- Functions exceeding 50 lines
- Files exceeding 800 lines
- Line length exceeding 88 characters

### 6. Imports (MEDIUM)

- Using `import logging` instead of `from synthorg.observability import get_logger`

### 7. Error Handling (MEDIUM)

- Silently swallowing errors
- Not logging at WARNING/ERROR before raising
- Not logging state transitions at INFO

## Severity Levels

- **HIGH**: Convention violation that affects correctness or consistency
- **MEDIUM**: Style deviation from project standards
- **LOW**: Minor preference

## Report Format

For each finding:

```text
[SEVERITY] file:line -- Convention violated
  Found: What the code does
  Required: What the convention demands
  Ref: Section of CLAUDE.md or design spec
```

End with summary count per severity.
