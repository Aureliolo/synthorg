---
title: Implicit Conventions
description: Patterns followed across the SynthOrg codebase that are not codified in CLAUDE.md but are universally observed.
---

# Implicit Conventions

`CLAUDE.md` codifies the rules we enforce with hooks and review agents.
This page captures the *implicit* conventions: patterns followed
across the codebase by precedent rather than enforcement. New code
should follow them; deviations should be justified in the diff.

## 1. Repository CRUD signature pattern

Every repository protocol exposes the same five-method shape, returning
`bool` from `delete` so callers can distinguish "removed one row" from
"id did not exist" without raising.

```python
class ApprovalRepository(Protocol):
    async def save(self, item: ApprovalItem) -> None: ...
    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None: ...
    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]: ...
    async def delete(self, approval_id: NotBlankStr) -> bool: ...
```

Reference: `src/synthorg/persistence/approval_protocol.py:34-73`.

## 2. Service lifecycle method symmetry

Long-lived services own a private `asyncio.Lock` named
`_lifecycle_lock` (separate from any hot-path lock) and expose
symmetric `async start()` / `async stop()` methods. Both must be
held across the full body of their respective method per
[lifecycle-sync.md](lifecycle-sync.md). Examples:
`src/synthorg/workers/worker.py`,
`src/synthorg/integrations/health/prober.py`.

## 3. API response wrapping

Controllers return one of two shapes:

* `ApiResponse[T]`: success-only path with no header or status-code
  customization. Litestar serializes it as `{"data": ..., "error": null,
  "success": true}`.
* `Response[ApiResponse[T]]`: only when status code or response
  headers must be customized (e.g. setting a `Location` header on a
  201, attaching a `Retry-After` header on a 429).

Reference: `src/synthorg/api/dto.py`, almost every controller under
`src/synthorg/api/controllers/`.

## 4. `@model_validator(mode="after")` is the default

`mode="after"` runs against the constructed model and is the default
choice. `mode="before"` is reserved for normalizing inputs the caller
might pass in non-canonical shape (lists vs tuples, dirty strings,
missing aliases). When using `mode="before"`, **never** mutate the
input dict in place; return a new dict via `{**data, key: value}`.
The four sites flagged by the 2026-04-25 audit have been converted;
the new immutability tests in `tests/unit/{api,tools}/...` lock
in the pattern.

## 5. Event constant module imports

Every observability event is defined as a `Final[str]` constant under
`src/synthorg/observability/events/<domain>.py` and imported by name
(never by string literal) from the consumer.

```python
from synthorg.observability.events.workers import (
    WORKERS_DISPATCHER_CLAIM_ENQUEUED,
    WORKERS_DISPATCHER_PUBLISH_EXHAUSTED,
    WORKERS_DISPATCHER_PUBLISH_FAILED,
    WORKERS_DISPATCHER_PUBLISH_RETRYING,
    WORKERS_DISPATCHER_QUEUE_NOT_RUNNING,
)
```

Reference: `src/synthorg/workers/dispatcher.py:19-25`.

## 6. Domain error hierarchies

Each domain owns `errors.py` with a base error class carrying
`status_code` / `error_code` / `error_category` / `retryable` as
`ClassVar`s; subclasses inherit and override only the fields that
change. The HTTP exception handler keys off the base class so a new
subclass automatically inherits the correct status mapping.

References:

* `src/synthorg/budget/errors.py`: `BudgetExhaustedError` family.
* `src/synthorg/communication/errors.py`: `CommunicationError`
  family.
* `src/synthorg/engine/errors.py`: `EngineError` family.

## 7. Module file structure

Every business-logic module follows the same top-down ordering:

1. Module docstring.
2. Imports: stdlib, then third-party, then internal, alphabetical
   within each group.
3. `logger = get_logger(__name__)` immediately after imports.
4. Module-level `Final` constants (private prefixed with `_`).
5. Public types (Pydantic models, dataclasses, enums).
6. Public functions / classes.
7. Private helpers (prefixed with `_`).

Reference: `src/synthorg/communication/bus/memory.py`.

## 8. Frozen `ConfigDict` pattern

Every Pydantic model declares
`model_config = ConfigDict(frozen=True, allow_inf_nan=False)`. Request
DTOs additionally set `extra="forbid"` so unknown keys are rejected
instead of silently ignored. Combined with the framework's `frozen`
guarantee this gives us the "create new objects, never mutate
existing ones" property the immutability covenant relies on.

References: 30+ occurrences across `src/synthorg/`. Canonical example:
`src/synthorg/approval/models.py:28`.

## See also

* [persistence-boundary.md](persistence-boundary.md): repository /
  service / controller layering.
* [lifecycle-sync.md](lifecycle-sync.md): `_lifecycle_lock` rule.
* [pluggable-subsystems.md](pluggable-subsystems.md): protocol +
  strategy + factory + config discriminator pattern.
