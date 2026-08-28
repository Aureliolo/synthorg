---
title: Error Reference
description: RFC 9457 structured error responses, content negotiation, and error taxonomy.
---

# Error Reference

SynthOrg's API returns structured error responses following
[RFC 9457 (Problem Details for HTTP APIs)](https://www.rfc-editor.org/rfc/rfc9457).
Every error includes machine-readable metadata that agents can use for
programmatic error handling and autonomous retry logic.

This page is the API consumer's reference: response shape, content
negotiation, and the error-code tables. For the stable problem-type URIs and
the `NotFoundError` class hierarchy behind these responses, see the
[Error Code Reference](reference/errors.md).

---

## Content Negotiation

The API supports two response formats for errors:

| Accept Header | Response Format |
|---------------|-----------------|
| `application/problem+json` | Bare RFC 9457 `ProblemDetail` body |
| `application/json` (or default) | `ApiResponse` envelope with `error_detail` |

### Requesting RFC 9457 Format

Send `Accept: application/problem+json` to receive bare RFC 9457 responses:

```bash
curl -H "Accept: application/problem+json" \
     -H "Authorization: Bearer $TOKEN" \
     http://localhost:3001/api/v1/tasks/nonexistent
```

Response (`404 Not Found`, `Content-Type: application/problem+json`):

```json
{
  "type": "https://synthorg.io/docs/errors#not_found",
  "title": "Resource Not Found",
  "status": 404,
  "detail": "Resource not found",
  "instance": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "error_code": 3001,
  "error_category": "not_found",
  "retryable": false,
  "retry_after": null
}
```

### Default Envelope Format

Without the `Accept` header (or with `application/json`), errors are wrapped
in the standard `ApiResponse` envelope:

```bash
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:3001/api/v1/tasks/nonexistent
```

Response (`404 Not Found`):

```json
{
  "data": null,
  "error": "Resource not found",
  "error_detail": {
    "detail": "Resource not found",
    "error_code": 3001,
    "error_category": "not_found",
    "retryable": false,
    "retry_after": null,
    "instance": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "title": "Resource Not Found",
    "type": "https://synthorg.io/docs/errors#not_found"
  },
  "success": false
}
```

---

## Error Categories

Each error belongs to one of eight categories. The `type` URI points to the
category-specific section of this page. The HTTP status is carried by the
raising error class, so a category spans every status its classes declare.

| Category | Title | HTTP Status | Type URI |
|----------|-------|-------------|----------|
| `auth` | Authentication Error | 401, 403 | `https://synthorg.io/docs/errors#auth` |
| `validation` | Validation Error | 400, 405, 422 | `https://synthorg.io/docs/errors#validation` |
| `not_found` | Resource Not Found | 404 | `https://synthorg.io/docs/errors#not_found` |
| `conflict` | Resource Conflict | 409 | `https://synthorg.io/docs/errors#conflict` |
| `rate_limit` | Rate Limit Exceeded | 429 | `https://synthorg.io/docs/errors#rate_limit` |
| `budget_exhausted` | Budget Exhausted | 402 | `https://synthorg.io/docs/errors#budget_exhausted` |
| `provider_error` | Provider Error | 502 by default; subclasses override | `https://synthorg.io/docs/errors#provider_error` |
| `internal` | Internal Server Error | 500, 503 | `https://synthorg.io/docs/errors#internal` |

---

## Error Codes

Error codes are 4-digit integers grouped by category (first digit = category).
The tables below carry the codes an API consumer meets on the common paths. The
complete set, including every domain-specific code, is in the
[Error Code Reference](reference/errors.md).

### 1xxx: Authentication { #auth }

| Code | Name | Description |
|------|------|-------------|
| 1000 | `UNAUTHORIZED` | Missing or invalid authentication credentials |
| 1001 | `FORBIDDEN` | Authenticated but insufficient permissions |
| 1002 | `SESSION_REVOKED` | Session has been revoked (logged out or force-revoked) |

### 2xxx: Validation { #validation }

| Code | Name | Description |
|------|------|-------------|
| 2000 | `VALIDATION_ERROR` | Application-level validation failure (e.g. invalid field values) |
| 2001 | `REQUEST_VALIDATION_ERROR` | Request structure/format validation failure |

### 3xxx: Not Found { #not_found }

| Code | Name | Description |
|------|------|-------------|
| 3000 | `RESOURCE_NOT_FOUND` | Requested resource does not exist |
| 3001 | `RECORD_NOT_FOUND` | Database record not found |
| 3002 | `ROUTE_NOT_FOUND` | API endpoint does not exist |

### 4xxx: Conflict { #conflict }

| Code | Name | Description |
|------|------|-------------|
| 4000 | `RESOURCE_CONFLICT` | Operation conflicts with current resource state |
| 4001 | `DUPLICATE_RECORD` | Attempted to create a resource that already exists |
| 4002 | `VERSION_CONFLICT` | ETag/If-Match mismatch (optimistic concurrency conflict) |

### 5xxx: Rate Limit { #rate_limit }

| Code | Name | Description |
|------|------|-------------|
| 5000 | `RATE_LIMITED` | Too many requests; back off and retry |
| 5001 | `PER_OPERATION_RATE_LIMITED` | Per-operation sliding-window rate limit exceeded for the endpoint's `operation` (see error body). Retry after `retry_after` seconds. |
| 5002 | `CONCURRENCY_LIMIT_EXCEEDED` | Per-operation concurrency cap reached: a previous long-running request for the same (operation, subject) bucket is still inflight. Retry after 1 second, or once the inflight request completes. |

### 6xxx: Budget Exhausted { #budget_exhausted }

| Code | Name | Description |
|------|------|-------------|
| 6000 | `BUDGET_EXHAUSTED` | Budget limit reached; no further spending allowed |

### 7xxx: Provider Error { #provider_error }

| Code | Name | Description |
|------|------|-------------|
| 7000 | `PROVIDER_ERROR` | Upstream LLM provider returned an error |
| 7001 | `PROVIDER_TIMEOUT` | Provider did not answer within the request budget (504) |
| 7002 | `PROVIDER_CONNECTION` | Network-level failure reaching the provider |
| 7003 | `PROVIDER_INTERNAL` | Provider returned a server-side error |
| 7004 | `PROVIDER_AUTHENTICATION_FAILED` | Provider rejected the brokered credential |

### 8xxx: Internal { #internal }

| Code | Name | Description |
|------|------|-------------|
| 8000 | `INTERNAL_ERROR` | Unexpected server error |
| 8001 | `SERVICE_UNAVAILABLE` | Required service is down or not configured |
| 8002 | `PERSISTENCE_ERROR` | Database or storage layer failure |

---

## Field Reference

### ProblemDetail (RFC 9457)

Returned when `Accept: application/problem+json`:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `string` | URI reference to this error category's documentation |
| `title` | `string` | Short, static, category-level summary |
| `status` | `int` | HTTP status code |
| `detail` | `string` | Human-readable, occurrence-specific explanation |
| `instance` | `string` | Request correlation ID for log tracing |
| `error_code` | `int` | Machine-readable 4-digit error code |
| `error_category` | `string` | Category identifier |
| `retryable` | `bool` | Whether the client should retry |
| `retry_after` | `int \| null` | Seconds to wait before retrying |

### ErrorDetail (Envelope)

Nested inside `ApiResponse.error_detail`:

| Field | Type | Description |
|-------|------|-------------|
| `detail` | `string` | Human-readable, occurrence-specific explanation |
| `error_code` | `int` | Machine-readable 4-digit error code |
| `error_category` | `string` | Category identifier |
| `retryable` | `bool` | Whether the client should retry |
| `retry_after` | `int \| null` | Seconds to wait before retrying |
| `instance` | `string` | Request correlation ID for log tracing |
| `title` | `string` | Short, static, category-level summary |
| `type` | `string` | URI reference to this error category's documentation |

---

## Retry Guidance

Agents should use `retryable` and `retry_after` for autonomous retry decisions:

- **`retryable: true`**: the request may succeed if retried after a delay
- **`retry_after`**: when set, wait this many seconds before retrying
- **`retryable: false`**: do not retry; the request needs to be fixed

Whether a request may be retried is a property of the raising error class, not
of the code range, so read `retryable` off the response rather than inferring
it from the code. The codes most often carrying `retryable: true`:

| Code | Name | Typical Cause |
|------|------|---------------|
| 5000 | `RATE_LIMITED` | Too many requests to the API |
| 5001 | `PER_OPERATION_RATE_LIMITED` | Per-operation sliding-window cap hit |
| 5002 | `CONCURRENCY_LIMIT_EXCEEDED` | Per-operation inflight cap hit (long-running op still running) |
| 7001 | `PROVIDER_TIMEOUT` | Upstream LLM provider did not answer in time |
| 7002 | `PROVIDER_CONNECTION` | Network-level failure reaching the provider |
| 7003 | `PROVIDER_INTERNAL` | Upstream provider returned a server-side error |
| 8001 | `SERVICE_UNAVAILABLE` | Transient service outage |

### Recommended Retry Strategy

1. Check `retryable`; if `false`, do not retry
2. If `retry_after` is set, wait that many seconds
3. Otherwise, use exponential backoff starting at 1 second
4. Cap retries at 3 attempts
5. On final failure, log the `instance` ID for human investigation

---

## Secret Redaction

No response body is a generic placeholder. Both 4xx and 5xx responses surface
the real error, secret-redacted: a 5xx `detail` is built by
`safe_error_description` (`{ExceptionType}: {message}`, credential patterns
stripped, length-bounded), and a 4xx `detail` runs the guard-authored or
middleware-authored message through `scrub_secret_tokens`. An operator reading
a failed action gets the actual condition rather than "contact support".

The `instance` correlation ID is on every error body and in every log line for
that request, so a response and its server-side context join on one value.
