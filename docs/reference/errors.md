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

This page is the developer-oriented reference for the problem-type URIs and the `NotFoundError` class hierarchy. For the response surface and content negotiation an API consumer sees, see the [Error Reference](../errors.md).

## Category URIs

| Category | `type` URI | Code range |
|----------|------------|------------|
| Authentication / authorisation | `https://synthorg.io/docs/errors#auth` | 1000-1999 |
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
| 1008 | `SESSION_NO_TOKEN` | No session cookie or bearer token on the request |
| 1009 | `SESSION_EXPIRED` | Session cookie / JWT decoded but past expiry |

## Validation (2xxx)

| Code | Name | When |
|------|------|------|
| 2000 | `VALIDATION_ERROR` | Generic validation failure |
| 2001 | `REQUEST_VALIDATION_ERROR` | Litestar-parsed body/params rejected |
| 2002 | `ARTIFACT_TOO_LARGE` | Upload exceeds `artifact.max_bytes` |
| 2003 | `TOOL_PARAMETER_ERROR` | Tool parameters failed schema validation |
| 2004 | `PROVIDER_TIER_COVERAGE_INSUFFICIENT` | Setup wizard cannot apply a template because no configured provider exposes any models |
| 2005 | `IMMUTABLE_FIELD_MISMATCH` | A restore/rollback would change an immutable field (e.g. agent id/name/department) |
| 2006 | `CHECKPOINT_ROLLBACK_UNAVAILABLE` | Fine-tune checkpoint rollback target is missing or unusable |
| 2007 | `CHECKPOINT_ROLLBACK_CORRUPT` | Fine-tune checkpoint rollback backup data is corrupt |
| 2008 | `RUN_HARD_CEILING_TOO_LOW` | Approved run hard-ceiling below the minimum |
| 2009 | `LIVING_DOC_VALIDATION_ERROR` | Living-doc payload failed validation |
| 2010 | `KNOWLEDGE_VALIDATION_ERROR` | Knowledge-source payload failed validation |
| 2011 | `RESEARCH_VALIDATION_ERROR` | Research request failed validation |
| 2012 | `BRAIN_ENTRY_VALIDATION_ERROR` | Project-brain entry failed validation |
| 2013 | `STEERING_KIND_INVALID` | Steering directive kind not recognised |
| 2014 | `STEERING_DIRECTIVE_FIELD_BLANK` | Steering directive required field blank |
| 2015 | `STEERING_TASK_PROJECT_MISMATCH` | Steering task does not belong to the project |

## Not Found (3xxx)

The NotFound hierarchy is rooted at `NotFoundError`. Each resource has a dedicated subclass that pins the right `ErrorCode` as a `ClassVar` (for example `TaskNotFoundError` in `synthorg.engine.errors`, `ConnectionNotFoundError` in `synthorg.integrations.errors`, and `ResourceNotFoundError` in `synthorg.core.domain_errors` as the 3000 fallback). Controllers raise the subclass directly, or via `require_resource_or_404(..., error_class=...)`, so the wire `error_code` is fixed by the class rather than a runtime argument.

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
| 3013 | `WORKFLOW_DEFINITION_NOT_FOUND` | Workflow definition record |
| 3014 | `AB_TEST_NOT_FOUND` | A/B test record for a proposal |
| 3015 | `BACKUP_NOT_FOUND` | Backup archive |
| 3016 | `MEMORY_ENTRY_NOT_FOUND` | Agent memory entry |
| 3017 | `CONVERSATION_NOT_FOUND` | Conversation record |
| 3018 | `LIVING_DOC_NOT_FOUND` | Living document |
| 3019 | `KNOWLEDGE_SOURCE_NOT_FOUND` | Knowledge source |
| 3020 | `RESEARCH_RUN_NOT_FOUND` | Research run record |
| 3021 | `CHARTER_NOT_FOUND` | Company charter |
| 3022 | `BRAIN_ENTRY_NOT_FOUND` | Project-brain entry |
| 3023 | `DELIVERABLE_RECEIPT_NOT_FOUND` | Deliverable receipt |
| 3024 | `API_KEY_NOT_FOUND` | API key |
| 3025 | `UPGRADE_RECOMMENDATION_NOT_FOUND` | Upgrade recommendation |
| 3026 | `GROUP_PARTICIPANT_UNKNOWN` | Group-chat participant not a known member |
| 3027 | `CHECKPOINT_NOT_FOUND` | Fine-tune checkpoint |
| 3028 | `FINE_TUNE_RUN_NOT_FOUND` | Fine-tune run record |
| 3029 | `AGENT_NOT_FOUND` | Registered agent |
| 3030 | `PERSONALITY_NOT_FOUND` | Agent personality |
| 3031 | `TRAINING_SESSION_NOT_FOUND` | Training session record |

All share the same `type` URI; the numeric code is the discriminator.

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
| 4008 | `WORKFLOW_EXECUTION_ALREADY_TERMINAL` | Cancel hit an execution already in a terminal status (no retry will succeed) |
| 4009 | `BACKUP_IN_PROGRESS` | A backup/restore operation is already running |
| 4010 | `CHECKPOINT_OPERATION_CONFLICT` | Checkpoint deploy/delete rejected (e.g. active checkpoint) |
| 4011 | `FINE_TUNE_RUN_ACTIVE` | A fine-tune run is already active (start/resume blocked) |
| 4012 | `TRAINING_PLAN_NOT_MODIFIABLE` | Training plan cannot be modified after execution or failure |
| 4013 | `BACKUP_UNRESTARTABLE` | Backup service stopped in an unrestartable state |
| 4014 | `AGENT_RUNTIME_NOT_CONFIGURED` | No LLM provider configured; agent runtime cannot execute |
| 4015 | `CONVERSATION_CLOSED` | Conversation is closed; no further messages or actions accepted |
| 4016 | `PROJECT_WORKSPACE_NOT_PROVISIONED` | Project workspace required but never provisioned by the git backend |
| 4017 | `LIVING_DOC_VERSION_CONFLICT` | Living-doc write lost an optimistic-concurrency race |
| 4018 | `ENVIRONMENT_BACKEND_UNAVAILABLE` | Declaration needs a sandbox backend that is not active (e.g. devcontainer on the subprocess backend) |
| 4019 | `CHARTER_ALREADY_DECIDED` | Late decision on an already-decided charter |
| 4020 | `CHARTER_NOT_EDITABLE` | Charter cannot be edited in its current state |
| 4021 | `BRAIN_ENTRY_REVISION_CONFLICT` | Project-brain entry optimistic-concurrency clash |
| 4022 | `HIRING_APPROVAL_REQUIRED` | Hiring request needs approval before instantiation |
| 4023 | `HIRING_REJECTED` | Hiring request was rejected |
| 4024 | `AGENT_ALREADY_REGISTERED` | Agent is already registered in the registry |
| 4025 | `PROMOTION_COOLDOWN_ACTIVE` | Promotion blocked by an active cooldown window |
| 4026 | `PROMOTION_APPROVAL_REQUIRED` | Promotion needs human approval before proceeding |
| 4027 | `PRUNING_UNRESTARTABLE` | Pruning service is unrestartable after a timed-out stop |
| 4028 | `SCALING_COOLDOWN_ACTIVE` | Scaling action blocked by an active cooldown window |
| 4029 | `ROLLBACK_MUTATION_DENIED` | Rollback mutator's underlying store refused the write |
| 4030 | `UPGRADE_RECOMMENDATION_ALREADY_DECIDED` | Approving/rejecting an already-decided recommendation |

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
| 6005 | `COST_FORECAST_APPROVAL_REQUIRED` | Run blocked pending cost-forecast approval |
| 6006 | `RUN_HARD_CEILING_EXCEEDED` | Per-brief hard ceiling exceeded |
| 6007 | `COST_FORECAST_REJECTED` | Cost forecast was rejected |
| 6008 | `RESEARCH_BUDGET_EXCEEDED` | Research budget exhausted |

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
| 7010 | `CONVERSATIONAL_PROPOSE_RESPONSE_INVALID` | Chief-of-Staff proposer returned an invalid response |
| 7011 | `CHARTER_INTERVIEW_RESPONSE_INVALID` | Charter interview agent returned an invalid response |

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
| 8009 | `FEATURE_NOT_IMPLEMENTED` | Active backend or deployment fundamentally does not implement the requested operation (501) |
| 8010 | `ARTIFACT_NO_STORAGE_BACKEND` | Artifact service was constructed without a storage backend; controller-helper misconfiguration |
| 8011 | `AGENT_IDENTITY_ROLLBACK_FAILED` | Unexpected server failure during agent-identity rollback |
| 8012 | `BACKUP_RESTORE_FAILED` | Restore operation failed (non-recoverable backend error) |
| 8013 | `BACKUP_MANIFEST_ERROR` | Backup manifest could not be parsed or validated |
| 8014 | `SETTINGS_ENCRYPTION_ERROR` | Internal error processing a sensitive (encrypted) setting |
| 8015 | `SINK_CONFIG_VALIDATION_ERROR` | Internal error validating an observability sink configuration |
| 8016 | `WORKER_DEAD_LETTER_ERROR` | Worker dead-letter handling failed |
| 8017 | `LIVING_DOC_INDEX_ERROR` | Living-doc RAG index operation failed |
| 8018 | `LIVING_DOC_COMMIT_ERROR` | Living-doc commit to the workspace failed |
| 8019 | `KNOWLEDGE_INGEST_ERROR` | Knowledge-source ingestion failed |
| 8020 | `KNOWLEDGE_RETRIEVAL_ERROR` | Knowledge retrieval failed |
| 8021 | `KNOWLEDGE_DEPENDENCY_ERROR` | Knowledge subsystem dependency unavailable |
| 8022 | `KNOWLEDGE_SOURCE_UNAVAILABLE` | Requested knowledge source is unavailable |
| 8023 | `ENVIRONMENT_ERROR` | Generic reproducible-environment failure |
| 8024 | `ENVIRONMENT_PROVISION_FAILED` | Environment setup/provisioning failed |
| 8025 | `ENVIRONMENT_DOCKER_BUILD_FAILED` | Devcontainer image build failed |
| 8026 | `RESEARCH_RUN_ERROR` | Research run failed |
| 8027 | `RESEARCH_RETRIEVAL_ERROR` | Research retrieval failed |
| 8028 | `RESEARCH_SYNTHESIS_ERROR` | Research synthesis failed |
| 8029 | `RESEARCH_UNAVAILABLE` | Research subsystem unavailable |
| 8030 | `FEATURE_DEPENDENCY_ERROR` | Required feature dependency unavailable |
| 8031 | `BRAIN_INDEX_ERROR` | Project-brain index operation failed |
| 8032 | `BRAIN_COMMIT_ERROR` | Project-brain commit to workspace failed |
| 8033 | `NARRATIVE_GENERATION_ERROR` | Narrative generation failed |
| 8034 | `NARRATIVE_SOURCE_UNAVAILABLE` | Narrative source data unavailable |
| 8035 | `DELIVERABLE_RECEIPT_BUILD_ERROR` | Deliverable receipt build failed |
| 8036 | `SINK_CONSTRUCTION_ERROR` | Observability sink construction failed |
| 8037 | `SUBWORKFLOW_CYCLE_ERROR` | Subworkflow reference graph contains a cycle |
| 8038 | `SUBWORKFLOW_DEPTH_EXCEEDED_ERROR` | Runtime subworkflow nesting exceeded the configured limit |
| 8039 | `TASK_ENGINE_NOT_RUNNING` | Task engine is not running; submission rejected (503) |
| 8040 | `TASK_ENGINE_QUEUE_FULL` | Task engine admission queue is full (503) |
| 8041 | `CONVERSATIONAL_PROPOSE_UNAVAILABLE` | Chief-of-Staff propose mode not wired (503) |
| 8042 | `GROUP_CHAT_UNAVAILABLE` | Group-chat mode not wired (503) |
| 8043 | `CHARTER_INTERVIEW_UNAVAILABLE` | Charter interview mode not wired (503) |

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

### Controller error re-mapping

Class metadata is the single source of truth for the wire contract; a
controller MUST NOT catch a domain error and re-raise it with a
different HTTP status or `error_code`. Doing so drops the originating
error's `retryable` signal and its discriminating code (see
[ADR-0014](../decisions/0014-error-contract-consolidation.md)).

- **Business-rule vs transient-backend.** When one call site can fail
  for a business reason or a transient backend reason, raise a distinct
  typed error for the business case so the contract reflects the cause.
  The checkpoint service raises `CheckpointActiveConflictError` (409,
  non-retryable) when rejecting a delete/deploy of the *active*
  checkpoint, while a transient `QueryError` (500, retryable) flows to
  `handle_persistence_error` untouched. Controllers must not collapse
  the transient `QueryError` into the 409.
- **Let retryable upstream failures propagate.** `oauth.py` does not
  catch `TokenExchangeFailedError`; its 502 + retryable metadata
  propagates so the client learns the upstream token endpoint failed and
  may retry.
- **Intentional uniform-404 (security override).**
  `connections.reveal_secret` raises `SecretRetrievalNotFoundError`
  (404, `RESOURCE_NOT_FOUND`) for both "connection absent" and "secret
  absent". Returning the same 404 either way prevents a caller from
  enumerating which connection names exist by probing secret-reveal
  error codes. This uniform-404 is deliberate; do not "fix" it into a
  more specific code, which would re-introduce the enumeration leak.

Each handler:

1. Calls `_log_error(request, exc, status=...)` for structured logging
   (WARNING for 4xx, ERROR + traceback for 5xx).
2. Returns `_build_response(...)` so the response carries the full RFC
   9457 envelope (or bare `application/problem+json` body when the
   client asks for it).
3. Scrubs the upstream message on 5xx.  4xx behaviour varies: domain
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

## Error-code uniqueness gate

Each `ErrorCode` maps to exactly one `DomainError` subclass, so a client
branching on `error_code` can always tell two conditions apart. The one
exception is an **inheritance alias**: a subclass that deliberately keeps
an ancestor's code (for example `ProviderModelNotFoundError`, which
subclasses `ModelNotFoundError` and inherits `MODEL_NOT_FOUND` 3011, and
the `NotFoundError` subclasses that share `RESOURCE_NOT_FOUND` 3000 as
the fallback). Two *unrelated* classes declaring the same code is a
violation.

`scripts/check_error_code_uniqueness.py` enforces this at pre-push and in
CI. For each concrete `DomainError` subclass it collects the
`(class, error_code, bases)` triple by AST across every `*.py` file
under `src/synthorg/` (subclasses can live in any module, not only
`errors.py`), groups by code, and fails a group unless its members
share a common `DomainError`
ancestor declaring that code, or the code is one of the shareable generic
codes (`INTERNAL_ERROR`, `VALIDATION_ERROR`, `RESOURCE_NOT_FOUND`,
`RESOURCE_CONFLICT`, `RECORD_NOT_FOUND`, ...). The gate is AST-only (no
import), uses git-ls-files discovery with a `Path.rglob` fallback, and
ships with zero offenders.

Per-line opt-out (reserved for documented twin classes that legitimately
share a code, e.g. a persistence-layer mirror of a domain error):

```python
class PersistenceVersionConflictError(QueryError):  # lint-allow: error-code-uniqueness -- twin of domain VersionConflictError
    ...
```

The justification after `--` is mandatory. See
[ADR-0014](../decisions/0014-error-contract-consolidation.md) for the
rationale and the four duplicate-code pairs this gate locked down.

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

## Interpreter-critical exception propagation

`MemoryError` and `RecursionError` are subclasses of `Exception`, so a
broad `except Exception:` block silently swallows them unless the
handler propagates them explicitly. Call
`synthorg.core.critical_errors.reraise_critical(exc)` as the first
statement of the broad handler:

```python
from synthorg.core.critical_errors import reraise_critical

try:
    ...
except Exception as exc:
    reraise_critical(exc)
    logger.warning(EVENT, error_type=type(exc).__name__, ...)
    raise QueryError(msg) from exc
```

`asyncio.CancelledError` is **not** routed through this helper because
it is a `BaseException`, not an `Exception`; a broad `except Exception:`
block never catches it. See
[Async concurrency conventions](conventions.md#11-async-concurrency-asynciotaskgroup-and-structured-concurrency)
for the surrounding pattern.

## Further reading

- [Design: security](../design/security.md): the SEC-1 rules behind the categories
- [REST API reference](../openapi/index.md): per-route examples plus a worked content-negotiation walkthrough
- `src/synthorg/core/error_taxonomy.py`: the authoritative `ErrorCategory` / `ErrorCode` enums + RFC 9457 helpers
- `src/synthorg/core/domain_errors.py`: the `DomainError` base + concrete subclasses (`NotFoundError`, `ConflictError`, `ValidationError`, ...)
- `src/synthorg/core/persistence_errors.py`: the `PersistenceError` hierarchy
- `src/synthorg/core/critical_errors.py`: the `reraise_critical` helper for the broad-except idiom
