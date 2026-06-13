/** Error utilities and user-friendly messages. */

import axios, { type AxiosError } from 'axios'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { ErrorCategory, ErrorCode, type ErrorDetail } from '@/api/types/errors'

const log = createLogger('errors')

/**
 * Cap on prose error messages reaching the user surface. Backend validators
 * can emit very long descriptions (e.g. enumerating every invalid field on
 * a bulk import); without a ceiling a multi-kilobyte string would blow up
 * toast and banner layouts. The truncation marker keeps the message
 * recognisably incomplete so users know to ask for the full detail in
 * support.
 */
const MAX_ERROR_MESSAGE_LEN = 1000

const GENERIC_FALLBACK_MESSAGE =
  'An unexpected error occurred. Please refresh the page or contact support if this persists.'

const NETWORK_ERROR_MESSAGE = 'Network error. Please check your connection.'

const INTERNAL_SERVER_ERROR_MESSAGE =
  'An unexpected server error occurred. Please try again later or '
  + 'contact support if this persists.'

const GENERIC_VALIDATION_MESSAGE =
  'Validation error. Please check the highlighted fields and try again.'

/**
 * Per-HTTP-status canned copy used by `getErrorMessage` when no
 * specialised handler claimed the response. 502 / 504 are grouped
 * because both manifest as the same transient upstream hop failure.
 */
const STATUS_FALLBACK_MESSAGES: Readonly<Record<number, string>> = {
  400: 'Invalid request. Please check your input.',
  401: 'Authentication required. Please log in.',
  403: 'You do not have permission to perform this action.',
  404: 'The requested resource was not found.',
  502: 'Temporary connectivity issue. Please retry shortly.',
  504: 'Temporary connectivity issue. Please retry shortly.',
}

/** Pydantic v1 / v2 leak patterns; see `_isPydanticishMessage` for use. */
const PYDANTIC_PHRASE_PATTERN =
  /(field required|value is not a valid|string too (short|long)|input should|string should|list should|dict should)/i

/**
 * 409 toast copy keyed by structured `ErrorCode`. Both DUPLICATE_*
 * variants share the duplicate-name copy; both VERSION_* variants share
 * the optimistic-concurrency copy. The lookup keeps `_handleConflict`
 * under the complexity cap by replacing the per-code `||` ladder with a
 * single table read.
 */
const CONFLICT_MESSAGES: Readonly<Partial<Record<ErrorCode, string>>> = {
  [ErrorCode.DUPLICATE_RECORD]:
    'A resource with this name already exists. Pick a different name.',
  [ErrorCode.ONTOLOGY_DUPLICATE]:
    'A resource with this name already exists. Pick a different name.',
  [ErrorCode.VERSION_CONFLICT]:
    'This resource was edited by someone else. Reload to see the latest version, then retry.',
  [ErrorCode.TASK_VERSION_CONFLICT]:
    'This resource was edited by someone else. Reload to see the latest version, then retry.',
}

/**
 * Toast titles keyed by structured `ErrorCategory`. `INTERNAL` is null
 * so the helper falls through to the HTTP-status / caller-fallback
 * branches: a 5xx-class internal failure carries a more specific
 * status-based message than the generic "Internal error" copy would.
 * Using `satisfies Record<ErrorCategory, ...>` makes the table
 * exhaustive: a new ErrorCategory value added to the backend breaks
 * the build until a title (or explicit fall-through) is supplied here.
 */
const CATEGORY_TITLES = {
  [ErrorCategory.AUTH]: 'Authentication failed',
  [ErrorCategory.VALIDATION]: 'Validation failed',
  [ErrorCategory.NOT_FOUND]: 'Not found',
  [ErrorCategory.CONFLICT]: 'Resource conflict',
  [ErrorCategory.RATE_LIMIT]: 'Rate limit reached',
  [ErrorCategory.BUDGET_EXHAUSTED]: 'Budget exhausted',
  [ErrorCategory.PROVIDER_ERROR]: 'Provider error',
  [ErrorCategory.INTERNAL]: null,
} as const satisfies Record<ErrorCategory, string | null>

/**
 * HTTP-status fallbacks for toast titles when no structured detail is
 * present. 403 is handled inline by `getCrudErrorTitle` (it short-
 * circuits the category switch with "Permission denied").
 */
const STATUS_TITLES: Readonly<Record<number, string>> = {
  401: 'Authentication failed',
  404: 'Not found',
  409: 'Resource conflict',
  422: 'Validation failed',
  429: 'Rate limit reached',
}

/**
 * Shape of the JSON envelope axios responses carry under
 * `response.data`. Captures only the fields the helpers in this module
 * read; backend additions stay tolerated by the index-signature.
 */
interface AxiosErrorData {
  error?: string
  error_detail?: ErrorDetail
  success?: boolean
}

/**
 * Format a millisecond duration as user-facing British English copy
 * for "try again in X" toasts. The granularity ladder is hour /
 * minute / second; sub-second waits round up to "a few seconds" so the
 * toast does not promise a precision the user cannot react to.
 */
function formatRetryAfter(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return 'a few seconds'
  const seconds = Math.max(1, Math.round(ms / 1000))
  if (seconds < 60) {
    if (seconds < 5) return 'a few seconds'
    return `${seconds} seconds`
  }
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60)
    return minutes === 1 ? '1 minute' : `${minutes} minutes`
  }
  const hours = Math.round(seconds / 3600)
  return hours === 1 ? '1 hour' : `${hours} hours`
}

/**
 * Parse the textual `Retry-After` header value into a wait duration in
 * milliseconds. Accepts both delta-seconds (`"90"`) and HTTP-date
 * (`"Wed, 21 Oct 2026 07:28:00 GMT"`) forms per RFC 9110 §10.2.3.
 * Returns null when the value cannot be interpreted as either form.
 */
function _parseRetryAfterValue(trimmed: string): number | null {
  if (/^\d+$/.test(trimmed)) {
    const seconds = Number.parseInt(trimmed, 10)
    return Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : null
  }
  const parsedDate = Date.parse(trimmed)
  if (!Number.isFinite(parsedDate)) return null
  return Math.max(0, parsedDate - Date.now())
}

/**
 * Read the `Retry-After` HTTP header value from an axios error
 * response and return the wait duration in milliseconds (or null
 * when the header is absent or unparseable). Distinct from
 * `parseRetryAfterMs` in `@/utils/retry-after`: that helper caps
 * the result against the auto-retry budget and returns a sentinel,
 * which is the wrong contract for user-facing toast copy where we
 * want the literal wait duration even when it exceeds the budget.
 */
function readRetryAfterHeaderMs(error: AxiosError): number | null {
  // ``AxiosResponse.headers`` is typed non-null, but coerced / faked error
  // objects can omit it; keep the optional chain.
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- headers may be absent on non-standard error objects
  const raw: unknown = error.response?.headers?.['retry-after']
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (trimmed === '') return null
  return _parseRetryAfterValue(trimmed)
}

/**
 * Duck-typed check for `ApiRequestError` instances without importing
 * the class. Importing `@/api/client` here would pull `axios.create()`
 * into utility modules that test code mocks `axios` for, breaking
 * the property-based tests in `errors.property.test.ts`.
 *
 * The class lives in `@/api/client` and sets `this.name = 'ApiRequestError'`
 * in its constructor; matching on the name plus the public
 * `errorDetail` field is sufficient for the read-only access path.
 */
function isApiRequestError(
  error: unknown,
): error is Error & { errorDetail: ErrorDetail | null } {
  if (
    !(error instanceof Error)
    || error.name !== 'ApiRequestError'
    || !('errorDetail' in error)
  ) {
    return false
  }
  // A malformed look-alike with ``errorDetail: undefined`` would pass a
  // presence-only check yet throw when a caller reads ``errorDetail.retry_after``;
  // require it to be null or an object so the read path is safe.
  const detail = (error as { errorDetail: unknown }).errorDetail
  return detail === null || typeof detail === 'object'
}

/** Check if an error is an Axios error. */
export function isAxiosError(error: unknown): error is AxiosError {
  return axios.isAxiosError(error)
}

/**
 * Backend rate limits are per-operation (per_op_rate_limit_from_policy),
 * not global; the toast hint tells the operator which operation hit the
 * cap so they do not assume the whole dashboard is throttled.
 */
function _handleRateLimited(error: AxiosError): string {
  const opHint = _urlOf(error).includes('/setup/complete')
    ? 'Too many setup completion attempts'
    : 'Too many requests for this operation'
  const ms = readRetryAfterHeaderMs(error)
  if (ms !== null && ms > 0) {
    return `${opHint}. Try again in ${formatRetryAfter(ms)}.`
  }
  return `${opHint}. Try again in a few seconds.`
}

function _handleServiceUnavailable(error: AxiosError): string {
  const ms = readRetryAfterHeaderMs(error)
  if (ms !== null) {
    return `The service is restarting. Try again in ${formatRetryAfter(ms)}.`
  }
  return 'The service is unavailable. Contact the operator if this persists.'
}

/** Return the lower-case request URL of an axios error, or empty string. */
function _urlOf(error: AxiosError): string {
  return error.config?.url ?? ''
}

/**
 * Branch on the structured `error_code` so duplicate /
 * version-conflict / generic-conflict cases each get actionable copy.
 * Setup completion is the most common 409 with no structured code
 * (RESOURCE_CONFLICT): the backend rejects a second `/setup/complete`
 * after the flag is already set. Surface that case with copy that
 * points operators at the right next step.
 */
function _handleConflict(error: AxiosError, data: AxiosErrorData | undefined): string {
  const code = data?.error_detail?.error_code
  const fromCode = code !== undefined ? CONFLICT_MESSAGES[code] : undefined
  if (fromCode !== undefined) return fromCode
  if (_urlOf(error).includes('/setup/complete')) {
    return 'Setup is already complete. Reload to see the current dashboard.'
  }
  return 'The resource state changed. Refresh the page and try again.'
}

/**
 * Pydantic v2 standardises on "Input should be ...", "String should
 * have at least/most N ...", "List should ...", "Dict should ..." in
 * addition to the v1-era "field required" / "value is not a valid" /
 * "string too short|long" phrasings. Catch the whole family so none of
 * them leak verbatim to end users.
 */
function _isPydanticishMessage(raw: string): boolean {
  return (
    /validation error for /i.test(raw)
    || PYDANTIC_PHRASE_PATTERN.test(raw)
  )
}

/** Return `value` trimmed, or null if it is not a non-blank string. */
function _trimmedOrNull(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/**
 * 422 prefers the structured `error_detail.detail` envelope (RFC 9457)
 * over the plain `data.error` string so the user sees the curated
 * message the backend chose for THIS validation failure rather than the
 * raw Pydantic surface. `data.error` is only surfaced when it does NOT
 * look like a raw Pydantic ValueError string.
 */
function _handleValidation(data: AxiosErrorData | undefined): string {
  const structuredDetail = _trimmedOrNull(data?.error_detail?.detail)
  if (structuredDetail !== null) return structuredDetail
  const raw = _trimmedOrNull(data?.error)
  if (raw !== null && !_isPydanticishMessage(raw)) return raw
  return GENERIC_VALIDATION_MESSAGE
}

/**
 * Specialised per-status handlers for axios errors where the canned
 * `STATUS_FALLBACK_MESSAGES` table is not enough. Returns null when no
 * specialised handler applies, so the caller can fall through to the
 * generic `data.error` / status-table / network / 5xx ladder.
 *
 * 409 / 429 / 503 are differentiated BEFORE the generic `data.error`
 * early-return so a backend that always populates `data.error` does not
 * flatten the structured copy below into a single uninformative line.
 * 422 is in this set because its structured `error_detail.detail`
 * wins inside `_handleValidation`.
 */
function _handleSpecialisedAxiosStatus(
  error: AxiosError,
  data: AxiosErrorData | undefined,
  status: number | undefined,
): string | null {
  if (status === 429) return _handleRateLimited(error)
  if (status === 503) return _handleServiceUnavailable(error)
  if (status === 409) return _handleConflict(error, data)
  if (status === 422) return _handleValidation(data)
  return null
}

/** True when `status` is in the 4xx client-error band. */
function _isClientStatus(status: number | undefined): status is number {
  return status !== undefined && status >= 400 && status < 500
}

/**
 * For 4xx (non-422; 422 is handled in `_handleValidation`), surface the
 * backend's `data.error` string verbatim when present. Returns null
 * when no usable string is available.
 */
function _extractBackendClientError(
  data: AxiosErrorData | undefined,
  status: number | undefined,
): string | null {
  if (!_isClientStatus(status)) return null
  // Empty / whitespace `data.error` must not short-circuit the
  // fallback chain; blank strings would otherwise surface as an
  // empty user-visible message in `_formatAxiosErrorMessage`.
  return _trimmedOrNull(data?.error)
}

/** Canned per-status copy from `STATUS_FALLBACK_MESSAGES`, or null. */
function _statusFallback(status: number | undefined): string | null {
  if (status === undefined) return null
  return STATUS_FALLBACK_MESSAGES[status] ?? null
}

/**
 * Final ladder when no specialised handler / backend string / status-
 * table entry matched: network errors, generic client errors, and
 * the 5xx escalation message. 5xx that did not match a specialised
 * handler (500, 505, ...): generic message + escalation hint avoids
 * leaking server internals while signalling whether to retry
 * transiently or escalate.
 */
function _fallbackAxiosMessage(error: AxiosError, status: number | undefined): string {
  if (!error.response) return NETWORK_ERROR_MESSAGE
  if (_isClientStatus(status)) {
    return `Request failed (${status}). Please check your input.`
  }
  return INTERNAL_SERVER_ERROR_MESSAGE
}

function _formatAxiosErrorMessage(error: AxiosError): string {
  const status = error.response?.status
  const data = error.response?.data as AxiosErrorData | undefined
  return (
    _handleSpecialisedAxiosStatus(error, data, status)
    ?? _extractBackendClientError(data, status)
    ?? _statusFallback(status)
    ?? _fallbackAxiosMessage(error, status)
  )
}

/**
 * Pass-through for plain Error instances. JSON-shaped messages are
 * suppressed because they typically carry a backend stack trace or
 * structured envelope leaked through to the client. Plain prose passes
 * through up to MAX_ERROR_MESSAGE_LEN characters so genuine long
 * validation messages reach the user without breaking layouts when an
 * upstream emits a multi-kilobyte description.
 */
function _formatStandardErrorMessage(error: Error): string {
  const msg = error.message
  if (msg && !/^\{/.test(msg)) {
    if (msg.length <= MAX_ERROR_MESSAGE_LEN) return msg
    return `${msg.slice(0, MAX_ERROR_MESSAGE_LEN)}…`
  }
  log.warn(
    'Error message suppressed (JSON-shaped)',
    sanitizeForLog({ preview: msg.slice(0, 300) }),
  )
  return GENERIC_FALLBACK_MESSAGE
}

/**
 * Surface the structured `retry_after` carried on an `ApiRequestError`
 * (thrown by `unwrap`, NOT an AxiosError, so the header-reading
 * `_handleRateLimited` path never runs for it). Without this the
 * seconds value the backend parsed into `error_detail.retry_after` is
 * discarded and a rate-limited mutation toast shows only the bare
 * message with no wait guidance. Mirrors the "Try again in X" copy the
 * AxiosError path produces from the `Retry-After` header.
 */
function _formatApiRequestErrorMessage(
  error: Error & { errorDetail: ErrorDetail | null },
): string {
  const base = _formatStandardErrorMessage(error)
  const detail = error.errorDetail
  if (detail === null) return base
  const seconds = detail.retry_after
  if (
    detail.error_category === ErrorCategory.RATE_LIMIT
    && seconds !== null
    && seconds > 0
  ) {
    const trimmed = base.replace(/[.\s]+$/, '')
    return `${trimmed}. Try again in ${formatRetryAfter(seconds * 1000)}.`
  }
  return base
}

/**
 * Extract a user-friendly error message from any error.
 * Filters raw 5xx backend error strings to prevent leaking internal details.
 */
export function getErrorMessage(error: unknown): string {
  if (isAxiosError(error)) return _formatAxiosErrorMessage(error)
  if (isApiRequestError(error)) return _formatApiRequestErrorMessage(error)
  if (error instanceof Error) return _formatStandardErrorMessage(error)
  return GENERIC_FALLBACK_MESSAGE
}

/**
 * Extract structured error detail from an Axios error, if present.
 * Returns null for non-API errors or when the backend did not
 * include structured error metadata.
 */
export function getErrorDetail(error: unknown): ErrorDetail | null {
  if (isApiRequestError(error)) {
    return error.errorDetail
  }
  if (!isAxiosError(error)) return null
  const data = error.response?.data as
    | { error_detail?: ErrorDetail }
    | undefined
  return data?.error_detail ?? null
}

/**
 * Convenience accessor: pull `error_detail.error_code` from any thrown
 * error shape the API surface produces (Axios 4xx/5xx,
 * `ApiRequestError` from `unwrap`). Returns null when the envelope
 * did not carry a structured code; callers fall back to the human-
 * readable message in that case.
 *
 * Use this when the UI wants to discriminate on a specific code
 * (e.g. `ErrorCode.PROVIDER_TIER_COVERAGE_INSUFFICIENT`) to surface
 * a targeted action instead of a generic Retry button.
 */
export function getErrorCode(error: unknown): ErrorCode | null {
  return getErrorDetail(error)?.error_code ?? null
}

/**
 * Pick a category-aware toast title prefix for CRUD failures.
 *
 * Auth / validation / conflict / rate-limit failures get specific
 * titles; everything else falls back to the caller's generic
 * `Failed to {action} {entity}` shape. The category derives from
 * `error_category` on the structured envelope, with HTTP-status
 * fallbacks for network errors that never carry a structured
 * ErrorDetail.
 *
 * Returns an object so callers can keep their existing
 * `description: getErrorMessage(err)` line and only swap the title.
 */
function _statusFromError(error: unknown): number | undefined {
  return isAxiosError(error) ? error.response?.status : undefined
}

function _titleFromDetail(detail: ErrorDetail | null): string | null {
  if (detail === null) return null
  return CATEGORY_TITLES[detail.error_category]
}

function _titleFromStatus(status: number | undefined): string | null {
  if (status === undefined) return null
  return STATUS_TITLES[status] ?? null
}

export function getCrudErrorTitle(
  error: unknown,
  fallback: string,
): { title: string } {
  // 403 (authorization) is a distinct title from 401 (authentication):
  // the user IS authenticated, just not allowed. Resolve the status
  // FIRST so 403 short-circuits the structured-detail switch.
  const status = _statusFromError(error)
  if (status === 403) return { title: 'Permission denied' }
  const fromCategory = _titleFromDetail(getErrorDetail(error))
  if (fromCategory) return { title: fromCategory }
  const fromStatus = _titleFromStatus(status)
  if (fromStatus) return { title: fromStatus }
  return { title: fallback }
}

/**
 * Group an array of per-item failure reasons by identical text so a
 * batch operation surfaces "5× version mismatch; 2× not found" instead
 * of repeating the same line for every failed id.
 *
 * Ordering is insertion order of the first occurrence, which keeps the
 * most-recent reason visible at the head when callers feed reasons in
 * the order results came back.
 */
export function formatBatchErrors(reasons: readonly string[]): string {
  if (reasons.length === 0) return ''
  const counts = new Map<string, number>()
  for (const reason of reasons) {
    counts.set(reason, (counts.get(reason) ?? 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([reason, count]) => (count === 1 ? reason : `${count}× ${reason}`))
    .join('; ')
}
