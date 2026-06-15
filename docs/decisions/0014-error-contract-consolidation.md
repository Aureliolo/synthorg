# ADR-0014: Error-contract consolidation

## Status

Accepted. Implemented in issue #2344 (the duplication and error-contract child of
the #2341 audit cluster). Issue #2335 defers its error-path work to this ADR,
which therefore lands first.

## Context

The #2341 audit found the domain-error-to-HTTP contract had drifted in ways the
existing `check_domain_error_hierarchy.py` gate did not catch:

- **Controllers re-mapping errors by hand.** Several controllers wrapped a
  service or persistence error in a fresh `except` block that assigned a
  different HTTP status, dropping the originating error's `error_code`, its
  `retryable` signal, or both. A transient `QueryError` (500, retryable) caught
  and re-raised as a `409 Conflict` told the client "do not retry" for what was
  actually a retryable backend blip; an OAuth `TokenExchangeFailedError` (502,
  retryable) was swallowed into a generic failure.
- **Two unrelated error classes sharing one `ErrorCode`.** Four code pairs
  collided, so a client branching on `error_code` could not tell the two
  conditions apart, and nothing stopped a fifth collision from being added.
- **A genuine but undocumented uniform-404.** `connections.reveal_secret`
  intentionally returns 404 for both "connection absent" and "secret absent" to
  prevent connection-existence enumeration, but it did so through a generic
  `NotFoundError` with no typed marker or recorded rationale.

The central handler `handle_domain_error` (`api/exception_handlers.py`) already
performs generic MRO + ClassVar dispatch over `status_code`, `error_code`,
`error_category`, and the retryable / `retry_after` metadata, and `DomainError`
is registered in `EXCEPTION_HANDLERS`. New typed subclasses are therefore picked
up automatically with no handler-table edits. The drift was not a missing
mechanism; it was controllers bypassing it and a missing uniqueness guard.

## Decision

### 1. Class metadata is the single source of truth

A `DomainError` subclass declares its wire contract once, as ClassVars
(`status_code`, `error_code`, `error_category`, `is_retryable` / `retry_after`,
`default_message`). Controllers do not re-map status or code in `except` blocks;
they let the error propagate (or raise a more specific typed subclass) so
`handle_domain_error` reads the metadata off the class. The audit's ad-hoc
translation blocks were removed:

- `memory/checkpoints.py` no longer catches `QueryError` and promotes it to
  `CheckpointOperationConflictError` (409); a transient `QueryError` keeps its
  500 + retryable contract.
- `workflow_executions.py` no longer translates `WorkflowExecutionNotFoundError`
  to a bare `NotFoundError`; the `logger.warning` moves before a bare re-raise so
  `WORKFLOW_EXECUTION_NOT_FOUND` reaches the wire with its own 404.

### 2. Typed errors for business-rule versus transient-backend conditions

Where one call site could fail for a business reason (a rule the service
enforces) or a transient backend reason (a query that happened to fail), the two
are split into distinct typed errors so the contract reflects the cause:

- `CheckpointActiveConflictError(CheckpointOperationConflictError)` (409,
  non-retryable) is raised by the checkpoint service when a delete or deploy
  targets the *active* checkpoint -- a business-rule rejection. A transient
  backend fault on the same path stays a `QueryError` (500, retryable) and flows
  to `handle_persistence_error` untouched.

### 3. Intentional uniform-404 security override, made typed and documented

`SecretRetrievalNotFoundError(NotFoundError)` (404, non-retryable,
`RESOURCE_NOT_FOUND`) is raised by `connections.reveal_secret` for the
deliberate uniform-404. Returning the same 404 whether the connection is absent
or the secret-backend lookup misses prevents a caller from enumerating which
connection names exist by probing secret-reveal error codes. The class name and
its docstring, plus the rows in `docs/reference/errors.md`, record the rationale
so the uniform-404 is not mistaken for a bug and "fixed" into an information
leak.

### 4. One `ErrorCode` per class, with an inheritance-alias exception

Every concrete `DomainError` subclass that declares an `error_code` ClassVar owns
a unique `ErrorCode`, except where a subclass deliberately keeps an ancestor's
code (an inheritance alias, e.g. a `NotFoundError` subclass that stays
`RESOURCE_NOT_FOUND`). The four colliding pairs were resolved:

- **Artifact too-large / storage-full.** The boundary-only
  `ArtifactRejectedTooLargeError` / `ArtifactStorageRejectedFullError` classes
  were deleted; the persistence `ArtifactTooLargeError` (413) and
  `ArtifactStorageFullError` (507) propagate from the controller directly, each
  the sole owner of its code. They are registered above `PersistenceError` in
  `EXCEPTION_HANDLERS` so MRO does not map them to 500.
- **Provider model not-found.** `ProviderModelNotFoundError` now subclasses
  `ModelNotFoundError`, inheriting `MODEL_NOT_FOUND` (3011) -- the same wire code
  as its sibling, as an explicit inheritance alias.
- **Project not-found.** `WorkProjectNotFoundError` was deleted;
  `ProjectNotFoundError` accepts an optional message plus an optional
  `project_id`, and every raise / catch site was repointed.

`scripts/check_error_code_uniqueness.py` (pre-push + CI) is the enforcement gate.
For each concrete `DomainError` subclass it collects the
`(class, error_code, bases)` triple by AST across `**/errors.py`,
`core/domain_errors.py`, and
`core/persistence_errors.py`, groups by code, and fails a group unless its
members share a common `DomainError` ancestor declaring that code (the
inheritance-alias case) or the code is one of the shareable generic codes
(`INTERNAL_ERROR`, `VALIDATION_ERROR`, `RESOURCE_NOT_FOUND`, ...). The per-line
opt-out `# lint-allow: error-code-uniqueness -- <reason>` is reserved for
documented twin classes. The gate ships clean with zero offenders.

### 5. `TokenExchangeFailedError` propagates 502 + retryable

The OAuth controller's `except TokenExchangeFailedError` block was dropped. The
class already carries 502 + retryable metadata, so removing the catch lets
`handle_domain_error` surface the honest "upstream token endpoint failed, retry"
contract instead of collapsing it into a generic failure. The
`OIDCVerificationError` / `InvalidStateError` -> 422 mappings stay (those are
genuine client-input faults, not upstream blips).

## Consequences

- The error-to-HTTP contract lives on the error classes; a controller that wants
  a different status must raise a different typed error, which makes the change
  visible and reviewable rather than buried in an `except` block.
- Clients can branch on `error_code` unambiguously, and the uniqueness gate stops
  a new collision from being added silently.
- The uniform-404 security override is typed and documented, so it survives
  future "consistency" refactors.
- The gate ships with its enforcement (pre-push, CI, a `convention_gate_map.yaml`
  entry, and a `(MANDATORY)` CLAUDE.md paragraph), per the Convention Rollout
  rule, so the meta-gate `check_convention_gate_inventory.py` passes.

## Related

- ADR-0001 (repository protocol consolidation) -- the persistence-error classes
  whose codes this ADR de-duplicates.
- ADR-0009 / ADR-0013 (import-layering contracts, dependency inversion) -- the
  `UserService` constraint-error translation referenced by the shared constraint
  mapper lives behind the controller-to-service boundary those ADRs established.
- Issue #2335 -- defers its 147 error-path work to this ADR.
