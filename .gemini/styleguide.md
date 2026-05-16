# SynthOrg Review Style Guide

This repository is **Python 3.14+ first**. Apply the project conventions in
[`CLAUDE.md`](../CLAUDE.md) (root), [`web/CLAUDE.md`](../web/CLAUDE.md), and
[`cli/CLAUDE.md`](../cli/CLAUDE.md) as authoritative. Read those files before
reviewing; they take precedence over generic best-practice defaults.

## Python 3.14 specifics (do NOT flag these as bugs)

### PEP 649: deferred evaluation of annotations is default

Python 3.14 ships PEP 649, which makes function and class annotations
**lazily evaluated by default**. Symbols referenced only in annotations do
**not** need to be imported at runtime. They can live inside
`if TYPE_CHECKING:` blocks and the annotation will still resolve via
`typing.get_type_hints()` or `inspect.get_annotations(eval_str=True)`.

**This codebase deliberately does NOT use `from __future__ import annotations`**
because PEP 649 supersedes it. The root `CLAUDE.md` codifies this:

> No `from __future__ import annotations` (3.14 has PEP 649).

Concretely, the following pattern is **correct and intentional**. Never flag
it as a `NameError` risk:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synthorg.memory.embedding.cancellation import CancellationToken
    from synthorg.memory.embedding.fine_tune_models import EvalMetrics


async def encode(
    *,
    cancellation: CancellationToken | None,  # unquoted; resolves lazily
) -> EvalMetrics:                              # unquoted; resolves lazily
    ...
```

Do **not** suggest replacing `CancellationToken | None` with the string form
`"CancellationToken | None"`. Do **not** suggest moving the import out of the
`TYPE_CHECKING` block "to avoid `NameError`". Neither is required on Python
3.14, and the established project convention is the unquoted form.

If you genuinely believe an annotation will be evaluated eagerly (e.g. it
appears in a dataclass field default, a `TypeAlias`, a `cast()`, a
`Pydantic` `Field` annotation read at class-definition time, or a
`typing.NewType()`), say so explicitly and cite the exact eager-evaluation
site. Do not blanket-flag every annotation.

### PEP 758: parenthesisless multi-exception `except`

PEP 758 makes `except A, B:` (comma-separated, no parens) valid Python.
This codebase uses that form intentionally. The most common example is the
project-wide system-error propagation pattern:

```python
except builtins.MemoryError, RecursionError:
    raise
```

Do not flag the missing parentheses as a syntax error or stylistic issue.

### Other Python 3.14 idioms

- `Final` and `Final[int]` / `Final[float]` annotations on module-level
  named constants are the **only** allowlisted form for magic numbers per
  `scripts/check_no_magic_numbers.py`. The pattern
  `NAME: Final[int] = 512` is correct.
- `asyncio.TaskGroup` is the project's fan-out / fan-in primitive
  (per `CLAUDE.md`). Helpers catch `Exception` and re-raise
  `MemoryError` / `RecursionError`.

## Architecture and conventions (load from CLAUDE.md)

The root `CLAUDE.md` is the source of truth for:

- Mandatory rules (design spec, planning, persistence boundary,
  configuration precedence, no-hardcoded-values, doc numeric claims,
  test regression, regional defaults).
- Code conventions: comments WHY only; type hints required; Google-style
  docstrings; line length 88; functions < 50 lines; files < 800 lines;
  errors named `<Domain><Condition>Error` from `DomainError`.
- Pydantic v2 frozen models with `extra="forbid"` on API DTOs.
- Repository CRUD signatures: `save(entity)`, `get(id)`,
  `delete(id) -> bool`, `list_items(...)`, `query(...)`.
- Logging conventions (event names from `observability.events.<domain>`,
  structured kwargs, secret-log redaction via
  `safe_error_description(exc)`).
- Test conventions (markers, FakeClock seam, `mock_of[T]` helper,
  Hypothesis profiles).

Before flagging a finding, check whether it conflicts with `CLAUDE.md`. If
it does, the project convention wins; do not raise the finding.

## Doc numeric macros, narrow scope

The `<!--RS:NAME-->...<!--/RS-->` runtime-stats macro requirement is
**not** a blanket rule for every numeric literal in every doc file. It is
enforced by `scripts/check_doc_numeric_macros.py`, which scopes to **only**
these files:

- `README.md`
- `docs/index.md`
- `docs/roadmap/index.md`
- `docs/architecture/decisions.md`
- `docs/reference/convention-gates.md`

… and matches **only** digits adjacent to specific stat nouns
(`tests`, `providers`, `agents`, `stars`, `releases`) or stat keywords
(`Mem0`, `version`, `release`, `current`, `latest`).

Generic constants in unrelated docs (e.g. `max_length=512` in an embedding
guide, `batch_size=128` in a tuning page) are **out of scope** for the
gate. Do not suggest wrapping them in `<!--RS:...-->` markers unless they
appear in one of the scoped files and reference a tracked stat.

## What to focus on

When reviewing this repo, prioritise:

1. **Correctness bugs**: missing `await`s, exception-handling regressions,
   data-loss paths, race conditions in async code, off-by-one errors in
   indexing / pagination.
2. **Security**: untrusted-content wrapping (`wrap_untrusted()` from
   `engine.prompt_safety`), secret-log redaction (`safe_error_description`
   not `str(exc)`), SQL / command injection, persistence-boundary leaks.
3. **Public API contract drift**: Pydantic DTO field renames /
   removals without a migration path, OpenAPI shape changes, REST route
   signature changes.
4. **Real concurrency hazards**: cancellation tokens that are dropped
   on a code path that does I/O, blocking calls inside async, missing
   `_lifecycle_lock` on lifecycle services.
5. **Tests that don't actually test the change**: mocks at typed
   boundaries (use `mock_of[T]` instead), `MagicMock` at typed boundaries
   (blocked by `scripts/check_mock_spec.py`), `sleep()` instead of
   `asyncio.Event().wait()` for synchronisation.

Skip:

- Cosmetic / formatting nits (ruff handles those).
- Stylistic preferences not codified in `CLAUDE.md`.
- Hypothetical NameError / ImportError on TYPE_CHECKING annotations
  (covered above).
- "Add a docstring" suggestions on test files (ruff D rules are
  src-only by project policy).
