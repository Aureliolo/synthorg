---
title: Error Code Reference
description: RFC 9457 problem type URIs, numeric error codes, and the NotFoundError hierarchy used by the SynthOrg REST API.
---

# Error Code Reference

SynthOrg's REST API emits RFC 9457 [`Problem Details`](https://www.rfc-editor.org/rfc/rfc9457) responses on every error path. Every response carries three machine-readable fields that clients can discriminate on:

- `type`: a stable URI describing the error **category**
- `error_code`: an integer in one of the ranges below
- `error_category`: the category name matching the URI slug

Clients should dispatch on `error_code` (most specific) and fall back to `error_category` for generic handling. Messages and titles are human-readable and may change without notice; the code is the contract.

## Category URIs

| Category | `type` URI | Code range |
|----------|------------|------------|
| Authentication / authorization | `https://synthorg.io/docs/errors#auth` | 1000-1999 |
| Request validation | `https://synthorg.io/docs/errors#validation` | 2000-2999 |
| Resource not found | `https://synthorg.io/docs/errors#not_found` | 3000-3999 |
| Conflict / duplicate | `https://synthorg.io/docs/errors#conflict` | 4000-4999 |
| Rate limit / concurrency | `https://synthorg.io/docs/errors#rate_limit` | 5000-5999 |
| Budget exhausted | `https://synthorg.io/docs/errors#budget_exhausted` | 6000-6999 |
| Provider / integration failure | `https://synthorg.io/docs/errors#provider_error` | 7000-7999 |
| Internal / service unavailable | `https://synthorg.io/docs/errors#internal` | 8000-8999 |

## Authentication (1xxx)

| Code | Name | When |
|------|------|------|
| 1000 | `UNAUTHORIZED` | Missing or invalid session |
| 1001 | `FORBIDDEN` | Authenticated but not permitted |
| 1002 | `SESSION_REVOKED` | Session revoked by operator or user |
| 1003 | `ACCOUNT_LOCKED` | Too many failed login attempts |
| 1004 | `CSRF_REJECTED` | CSRF double-submit failed |
| 1005 | `REFRESH_TOKEN_INVALID` | Refresh rotation mismatch or expired |
| 1006 | `SESSION_LIMIT_EXCEEDED` | Per-user session cap reached |
| 1007 | `TOOL_PERMISSION_DENIED` | Agent not permitted to invoke the tool |

## Validation (2xxx)

| Code | Name | When |
|------|------|------|
| 2000 | `VALIDATION_ERROR` | Generic validation failure |
| 2001 | `REQUEST_VALIDATION_ERROR` | Litestar-parsed body/params rejected |
| 2002 | `ARTIFACT_TOO_LARGE` | Upload exceeds `artifact.max_bytes` |
| 2003 | `TOOL_PARAMETER_ERROR` | Tool parameters failed schema validation |

## Not Found (3xxx)

The NotFound hierarchy is driven by a single `NotFoundError` class with domain-specific `ErrorCode` members. Callers use :func:`synthorg.core.domain_errors.resource_not_found` to pick the right code without constructing an error subclass by hand.

| Code | Name | Resource |
|------|------|----------|
| 3000 | `RESOURCE_NOT_FOUND` | Fallback: the resource type isn't in the table below |
| 3001 | `RECORD_NOT_FOUND` | Generic DB row not found |
| 3002 | `ROUTE_NOT_FOUND` | HTTP path had no handler |
| 3003 | `PROJECT_NOT_FOUND` | Project |
| 3004 | `TASK_NOT_FOUND` | Task |
| 3005 | `SUBWORKFLOW_NOT_FOUND` | Sub-workflow definition |
| 3006 | `WORKFLOW_EXECUTION_NOT_FOUND` | Workflow execution record |
| 3007 | `CHANNEL_NOT_FOUND` | Communication channel |
| 3008 | `TOOL_NOT_FOUND` | Registered tool |
| 3009 | `ONTOLOGY_NOT_FOUND` | Ontology entry |
| 3010 | `CONNECTION_NOT_FOUND` | Integration connection |
| 3011 | `MODEL_NOT_FOUND` | Provider model |
| 3012 | `ESCALATION_NOT_FOUND` | Escalation queue entry |

All 13 share the same `type` URI; the numeric code is the discriminator.

## Conflict (4xxx)

| Code | Name | When |
|------|------|------|
| 4000 | `RESOURCE_CONFLICT` | Generic 409 (resource state mismatch) |
| 4001 | `DUPLICATE_RECORD` | Unique-constraint violation |
| 4002 | `VERSION_CONFLICT` | Optimistic-concurrency (ETag) mismatch |
| 4003 | `TASK_VERSION_CONFLICT` | Same, scoped to a task update |
| 4004 | `ONTOLOGY_DUPLICATE` | Duplicate ontology entity or alias |
| 4005 | `CHANNEL_ALREADY_EXISTS` | Channel name already taken |
| 4006 | `ESCALATION_ALREADY_DECIDED` | Late decision on a closed escalation |
| 4007 | `MIXED_CURRENCY_AGGREGATION` | Cross-currency aggregation attempted |

## Rate Limit (5xxx)

| Code | Name | When |
|------|------|------|
| 5000 | `RATE_LIMITED` | Global per-user / per-IP throttle tripped |
| 5001 | `PER_OPERATION_RATE_LIMITED` | Specific operation's `(max_requests, window)` budget exhausted |
| 5002 | `CONCURRENCY_LIMIT_EXCEEDED` | Too many in-flight requests for the op |

## Budget Exhausted (6xxx)

| Code | Name | When |
|------|------|------|
| 6000 | `BUDGET_EXHAUSTED` | Company-level budget hard stop |
| 6001 | `DAILY_LIMIT_EXCEEDED` | Prorated daily cap tripped |
| 6002 | `RISK_BUDGET_EXHAUSTED` | Per-risk-tier budget exceeded |
| 6003 | `PROJECT_BUDGET_EXHAUSTED` | Project-scoped budget hard stop |
| 6004 | `QUOTA_EXHAUSTED` | Metered feature quota reached |

## Provider / Integration (7xxx)

| Code | Name | When |
|------|------|------|
| 7000 | `PROVIDER_ERROR` | Generic upstream failure |
| 7001 | `PROVIDER_TIMEOUT` | Upstream timed out |
| 7002 | `PROVIDER_CONNECTION` | Network-level failure |
| 7003 | `PROVIDER_INTERNAL` | Provider returned 5xx |
| 7004 | `PROVIDER_AUTHENTICATION_FAILED` | Invalid credentials |
| 7005 | `PROVIDER_INVALID_REQUEST` | Provider rejected the request |
| 7006 | `PROVIDER_CONTENT_FILTERED` | Provider filtered the content |
| 7007 | `INTEGRATION_ERROR` | Non-LLM integration failure |
| 7008 | `OAUTH_ERROR` | OAuth exchange failed |
| 7009 | `WEBHOOK_ERROR` | Webhook receive/replay failure |

## Internal (8xxx)

| Code | Name | When |
|------|------|------|
| 8000 | `INTERNAL_ERROR` | Unspecified server error |
| 8001 | `SERVICE_UNAVAILABLE` | Dependent service not wired yet |
| 8002 | `PERSISTENCE_ERROR` | DB-level failure |
| 8003 | `ENGINE_ERROR` | Engine-layer failure |
| 8004 | `ONTOLOGY_ERROR` | Ontology subsystem failure |
| 8005 | `COMMUNICATION_ERROR` | Meeting/message bus failure |
| 8006 | `TOOL_ERROR` | Generic tool failure |
| 8007 | `ARTIFACT_STORAGE_FULL` | Artifact store at capacity |
| 8008 | `TOOL_EXECUTION_ERROR` | Tool runtime failure (subclass of `TOOL_ERROR`) |

## Content negotiation

Clients that set `Accept: application/problem+json` receive a bare RFC 9457 body. Clients that accept `application/json` receive an `ApiResponse` envelope with `error_detail` carrying the same fields. See the [API reference](../openapi/) for per-route examples.

## HTTP exception handler registration (MANDATORY)

Litestar resolves exception handlers by walking the raised exception's
MRO; the first matching type in `EXCEPTION_HANDLERS`
(`src/synthorg/api/exception_handlers.py`) wins. Domain error families
register a single base-class handler (e.g. `BackupError`,
`PersistenceError`, `OntologyError`) so every subtype maps to a
structured response without falling through to the catch-all
`Exception: handle_unexpected` (which would surface as a generic 500
without a domain-specific `error_code`).

**Controllers MUST NOT catch a domain error and build their own
`Response(...)` envelope.** Raise the typed domain error (declaring
`status_code` / `error_code` / `error_category` ClassVars on the
class) and let `handle_domain_error` produce the RFC 9457 envelope.
Enforced at pre-push by
`scripts/check_no_controller_response_for_domain_errors.py`.
Per-line opt-out:
`# lint-allow: controller-domain-response -- <reason>`.

Each handler:

1. Calls `_log_error(request, exc, status=...)` for structured logging
   (WARNING for 4xx, ERROR + traceback for 5xx).
2. Returns `_build_response(...)` so the response carries the full RFC
   9457 envelope (or bare `application/problem+json` body when the
   client asks for it).
3. Scrubs the upstream message on 5xx.  4xx behavior varies: domain
   handlers like `handle_backup_error` and `handle_domain_error` pass a
   user-safe exception message through, while several Litestar-side
   handlers intentionally return fixed public messages
   (`handle_record_not_found` -> `"Resource not found"`,
   `handle_not_authorized` -> `"Authentication required"`,
   `handle_permission_denied` -> `"Forbidden"`,
   `handle_not_found` -> `"Not found"`).  When in doubt, mirror the
   nearest existing handler in `exception_handlers.py`.

When introducing a new domain error family:

1. Add the base class to `EXCEPTION_HANDLERS` mapped to a dedicated
   `handle_<domain>_error` function.
2. Use `isinstance` dispatch inside the handler to map subtypes to
   specific HTTP status codes (`404`, `409`, etc.) before falling
   through to the structured `500` branch.
3. Register the entry **above** the catch-all `Exception:
   handle_unexpected` line in the dict; MRO ordering does not depend on
   dict insertion order, but readability does.
4. Add tests in `tests/unit/api/test_exception_handlers.py` covering
   each branch and a regression test for the catch-all.

## Domain-error-hierarchy gate

`scripts/check_domain_error_hierarchy.py` enforces the rule at pre-push
and in CI: every class definition under `src/synthorg/` whose direct
base is one of `Exception` / `RuntimeError` / `LookupError` /
`PermissionError` / `ValueError` / `TypeError` / `KeyError` /
`IndexError` / `AttributeError` / `OSError` / `IOError` is a violation
unless the class itself reaches `DomainError` via another base.

Only the *root* of a stdlib-rooted chain is flagged; migrating the root
to `DomainError` automatically corrects every descendant.

Per-line opt-out:

```python
class TsaError(Exception):  # lint-allow: domain-error-hierarchy -- RFC 3161 internals; observability stays stdlib-rooted
    ...
```

The justification after `--` is mandatory and must be non-empty. The
gate also accepts a frozen baseline file
(`scripts/domain_error_hierarchy_baseline.txt`) listing pre-existing
violations a rollout has not yet reached. The baseline shrinks
monotonically: any entry that no longer maps to a real violation is
reported as drift, so the file cannot harbour stale rows.

## Error-code constants - frontend integration (MANDATORY)

`src/synthorg/core/error_taxonomy.py` is the single source of truth
for `ErrorCode` and `ErrorCategory`. The dashboard imports the same
constants from `web/src/api/types/error-codes.gen.ts`, which is
generated from the Python enums by
`scripts/generate_error_codes_ts.py`.

When adding or renaming a code:

1. Edit `src/synthorg/core/error_taxonomy.py`.
2. Run `uv run python scripts/generate_error_codes_ts.py` and commit
   `web/src/api/types/error-codes.gen.ts` alongside the Python change.
3. Update any frontend call sites that newly want to discriminate on
   the code (import `ErrorCode` from `@/api/types/errors` and use the
   named member, never the raw integer).

The pre-push gate `scripts/check_error_codes_ts_in_sync.py` re-runs
the generator and fails if the committed `error-codes.gen.ts` differs
byte-for-byte from the freshly rendered output, so a backend code
addition that forgets the regeneration step cannot land.

Frontend code MUST import error codes through `@/api/types/errors`
(re-exported from the generated module). Inlining numeric error codes
(e.g. `error_code: 3000`) is forbidden -- name-based discrimination
keeps the `web/` and `src/synthorg/` sides in lockstep when codes are
renumbered.

## Further reading

- [Design: security](../design/security.md): the SEC-1 rules behind the categories
- [REST API reference](../openapi/index.md): per-route examples plus a worked content-negotiation walkthrough
- `src/synthorg/core/error_taxonomy.py`: the authoritative `ErrorCategory` / `ErrorCode` enums + RFC 9457 helpers
- `src/synthorg/core/domain_errors.py`: the `DomainError` base + concrete subclasses (`NotFoundError`, `ConflictError`, `ValidationError`, ...)
- `src/synthorg/core/persistence_errors.py`: the `PersistenceError` hierarchy
