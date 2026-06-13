import { AxiosError, type AxiosResponse } from 'axios'
import {
  formatBatchErrors,
  getCrudErrorTitle,
  getErrorCode,
  getErrorDetail,
  getErrorMessage,
  isAxiosError,
} from '@/utils/errors'
import { ErrorCategory, ErrorCode, type ErrorDetail } from '@/api/types/errors'

function makeAxiosError(
  status: number | undefined,
  data?: Record<string, unknown>,
  headers: Record<string, string> = {},
  url?: string,
): AxiosError {
  const config = { url: url ?? '' } as AxiosResponse['config']
  const error = new AxiosError(
    'Request failed',
    status ? 'ERR_BAD_RESPONSE' : 'ERR_NETWORK',
    config,
    undefined,
    status
      ? {
          status,
          data,
          headers,
          statusText: 'Error',
          config,
        } as AxiosResponse
      : undefined,
  )
  return error
}

function makeDetail(
  category: ErrorCategory,
  code: ErrorCode = ErrorCode.RESOURCE_NOT_FOUND,
): ErrorDetail {
  return {
    detail: 'test detail',
    error_code: code,
    error_category: category,
    retryable: false,
    retry_after: null,
    instance: 'req-x',
    title: 'Test',
    type: 'https://docs.example.com/errors/test',
  }
}

/**
 * Build a duck-typed `ApiRequestError` (the shape `unwrap` throws)
 * without importing the real class from `@/api/client`, which would
 * pull `axios.create()` into this test module. `isApiRequestError`
 * matches on `name === 'ApiRequestError'` plus the `errorDetail` field.
 */
function makeApiRequestError(message: string, detail: ErrorDetail | null): Error {
  const error = new Error(message)
  error.name = 'ApiRequestError'
  Object.assign(error, { errorDetail: detail })
  return error
}

describe('isAxiosError', () => {
  it('returns true for AxiosError', () => {
    expect(isAxiosError(makeAxiosError(400))).toBe(true)
  })

  it('returns false for regular Error', () => {
    expect(isAxiosError(new Error('test'))).toBe(false)
  })

  it('returns false for non-error values', () => {
    expect(isAxiosError('string')).toBe(false)
    expect(isAxiosError(null)).toBe(false)
    expect(isAxiosError(undefined)).toBe(false)
  })
})

describe('getErrorMessage', () => {
  it('returns 4xx backend error message when available', () => {
    const error = makeAxiosError(400, { error: 'Invalid name', success: false })
    expect(getErrorMessage(error)).toBe('Invalid name')
  })

  it('returns generic message for 400 without backend error', () => {
    const error = makeAxiosError(400)
    expect(getErrorMessage(error)).toBe('Invalid request. Please check your input.')
  })

  it('returns auth message for 401', () => {
    const error = makeAxiosError(401)
    expect(getErrorMessage(error)).toBe('Authentication required. Please log in.')
  })

  it('returns permission message for 403', () => {
    const error = makeAxiosError(403)
    expect(getErrorMessage(error)).toBe('You do not have permission to perform this action.')
  })

  it('returns not found message for 404', () => {
    const error = makeAxiosError(404)
    expect(getErrorMessage(error)).toBe('The requested resource was not found.')
  })

  it('returns a refresh-and-retry message for 409 with no structured code', () => {
    // 409 without a structured error_code falls through to the
    // generic concurrency copy.
    const error = makeAxiosError(409)
    expect(getErrorMessage(error)).toMatch(/refresh/i)
  })

  it('returns duplicate-name copy for 409 + DUPLICATE_RECORD', () => {
    const detail: ErrorDetail = {
      detail: 'name already taken',
      error_code: ErrorCode.DUPLICATE_RECORD,
      error_category: ErrorCategory.CONFLICT,
      retryable: false,
      retry_after: null,
      instance: 'req-409',
      title: 'Conflict',
      type: 'https://docs.example.com/errors/conflict',
    }
    const error = makeAxiosError(409, { error_detail: detail })
    expect(getErrorMessage(error)).toContain('already exists')
    expect(getErrorMessage(error)).toContain('different name')
  })

  it('returns version-conflict copy for 409 + VERSION_CONFLICT', () => {
    const detail: ErrorDetail = {
      detail: 'version mismatch',
      error_code: ErrorCode.VERSION_CONFLICT,
      error_category: ErrorCategory.CONFLICT,
      retryable: false,
      retry_after: null,
      instance: 'req-409v',
      title: 'Conflict',
      type: 'https://docs.example.com/errors/conflict',
    }
    const error = makeAxiosError(409, { error_detail: detail })
    expect(getErrorMessage(error)).toContain('edited by someone else')
  })

  it('returns validation message for 422 when no detail is present', () => {
    const error = makeAxiosError(422)
    expect(getErrorMessage(error)).toContain('Validation')
  })

  it('surfaces structured error_detail.detail for 422 when data.error is absent', () => {
    const detail: ErrorDetail = {
      detail: 'currency: invalid_code',
      error_code: ErrorCode.REQUEST_VALIDATION_ERROR,
      error_category: ErrorCategory.VALIDATION,
      retryable: false,
      retry_after: null,
      instance: 'req-422',
      title: 'Validation Failed',
      type: 'https://docs.example.com/errors/validation',
    }
    const error = makeAxiosError(422, { error_detail: detail })
    expect(getErrorMessage(error)).toBe('currency: invalid_code')
  })

  it('returns rate limit message for 429 with no Retry-After header', () => {
    const error = makeAxiosError(429)
    expect(getErrorMessage(error)).toContain('Too many requests')
  })

  it('surfaces Retry-After duration in 429 toast copy when header is present', () => {
    // Retry-After delta-seconds is formatted with British English
    // "X minutes" granularity, capped at the readable ceiling.
    const error = makeAxiosError(429, undefined, { 'retry-after': '90' })
    expect(getErrorMessage(error)).toContain('Try again in')
    expect(getErrorMessage(error)).toContain('2 minutes')
  })

  it('returns transient-503 copy when Retry-After is present', () => {
    const error = makeAxiosError(503, undefined, { 'retry-after': '15' })
    expect(getErrorMessage(error)).toContain('restarting')
    expect(getErrorMessage(error)).toContain('15 seconds')
  })

  it('returns sustained-503 copy when Retry-After is absent', () => {
    const error = makeAxiosError(503)
    expect(getErrorMessage(error)).toContain('unavailable')
    expect(getErrorMessage(error)).toContain('operator')
  })

  it('does NOT leak 5xx error body', () => {
    const error = makeAxiosError(500, { error: 'Internal: SQL deadlock on users table' })
    // Message refined to escalate on the unknown-5xx path; SQL detail
    // must still never reach the user.
    const message = getErrorMessage(error)
    expect(message).not.toContain('SQL')
    expect(message).not.toContain('deadlock')
    expect(message).toContain('contact support')
  })

  it('returns network error for no response', () => {
    const error = makeAxiosError(undefined)
    expect(getErrorMessage(error)).toBe('Network error. Please check your connection.')
  })

  it('returns transient connectivity hint for 502', () => {
    const error = makeAxiosError(502)
    expect(getErrorMessage(error)).toContain('connectivity')
  })

  it('returns transient connectivity hint for 504', () => {
    const error = makeAxiosError(504)
    expect(getErrorMessage(error)).toContain('connectivity')
  })

  it('mentions setup-completion in 429 toast when the request hit /setup/complete', () => {
    const error = makeAxiosError(429, undefined, {}, '/api/v1/setup/complete')
    expect(getErrorMessage(error)).toContain('setup completion')
  })

  it('mentions setup-completion in 409 toast when the request hit /setup/complete', () => {
    const error = makeAxiosError(409, undefined, {}, '/api/v1/setup/complete')
    expect(getErrorMessage(error)).toContain('Setup is already complete')
  })

  it('falls back to generic 422 message when data.error is a Pydantic-shaped string and detail is absent', () => {
    // Pydantic v2 phrasing such as "Input should be a valid integer" leaks
    // internal type names; the helper must suppress it and return the
    // curated message instead.
    const error = makeAxiosError(422, { error: 'Input should be a valid integer' })
    expect(getErrorMessage(error)).toBe(
      'Validation error. Please check the highlighted fields and try again.',
    )
  })

  it('surfaces clean (non-Pydantic) data.error string verbatim for 422 when structured detail is absent', () => {
    const error = makeAxiosError(422, { error: 'Project name must be unique within the workspace.' })
    expect(getErrorMessage(error)).toBe(
      'Project name must be unique within the workspace.',
    )
  })

  it('falls back to generic 4xx copy for an unmapped client status (418)', () => {
    const error = makeAxiosError(418)
    expect(getErrorMessage(error)).toBe('Request failed (418). Please check your input.')
  })

  it('returns 1-minute (not 1 minutes) for a 60s Retry-After', () => {
    const error = makeAxiosError(429, undefined, { 'retry-after': '60' })
    expect(getErrorMessage(error)).toContain('1 minute')
    expect(getErrorMessage(error)).not.toContain('1 minutes')
  })

  it('returns 1-hour (not 1 hours) for a 3600s Retry-After', () => {
    const error = makeAxiosError(429, undefined, { 'retry-after': '3600' })
    expect(getErrorMessage(error)).toContain('1 hour')
    expect(getErrorMessage(error)).not.toContain('1 hours')
  })

  it('parses HTTP-date Retry-After header into a future duration', () => {
    const future = new Date(Date.now() + 90_000).toUTCString()
    const error = makeAxiosError(429, undefined, { 'retry-after': future })
    expect(getErrorMessage(error)).toContain('Try again in')
  })

  it('appends the retry hint for an ApiRequestError rate-limit detail', () => {
    const detail = makeDetail(ErrorCategory.RATE_LIMIT, ErrorCode.RATE_LIMITED)
    const error = makeApiRequestError('Slow down', { ...detail, retry_after: 120 })
    const message = getErrorMessage(error)
    expect(message).toContain('Slow down')
    expect(message).toContain('Try again in')
    expect(message).toContain('2 minutes')
  })

  it('does not append a retry hint when ApiRequestError retry_after is null', () => {
    const detail = makeDetail(ErrorCategory.RATE_LIMIT, ErrorCode.RATE_LIMITED)
    const error = makeApiRequestError('Slow down', detail)
    expect(getErrorMessage(error)).toBe('Slow down')
  })

  it('leaves a non-rate-limit ApiRequestError message unchanged', () => {
    const detail = makeDetail(ErrorCategory.VALIDATION)
    const error = makeApiRequestError('Invalid field', { ...detail, retry_after: 30 })
    expect(getErrorMessage(error)).toBe('Invalid field')
  })

  it('returns Error.message for non-axios Error with short message', () => {
    expect(getErrorMessage(new Error('Something went wrong'))).toBe('Something went wrong')
  })

  it('passes through Error.message verbatim for long prose messages', () => {
    // The 200-char ceiling was dropped: long validation messages from
    // backend exceptions now reach the user instead of being replaced
    // with the generic fallback. JSON-shaped messages stay suppressed
    // (covered by the next test).
    const longMsg = 'x'.repeat(500)
    expect(getErrorMessage(new Error(longMsg))).toBe(longMsg)
  })

  it('truncates Error.message past ~1000 chars with an ellipsis', () => {
    // Without a ceiling a multi-kilobyte backend description (e.g. a
    // bulk-import validator naming every invalid row) would blow up
    // toast / banner layouts. The cap keeps the message recognisably
    // incomplete so users know to consult support for the full detail.
    const overLong = 'a'.repeat(2000)
    const result = getErrorMessage(new Error(overLong))
    expect(result).toHaveLength(1001)
    expect(result.endsWith('…')).toBe(true)
    expect(result.startsWith('aaaa')).toBe(true)
  })

  it('returns generic message for Error starting with {', () => {
    expect(getErrorMessage(new Error('{"internal":"data"}'))).toBe('An unexpected error occurred. Please refresh the page or contact support if this persists.')
  })

  it('returns generic message for non-error values', () => {
    expect(getErrorMessage('string')).toBe('An unexpected error occurred. Please refresh the page or contact support if this persists.')
    expect(getErrorMessage(42)).toBe('An unexpected error occurred. Please refresh the page or contact support if this persists.')
    expect(getErrorMessage(null)).toBe('An unexpected error occurred. Please refresh the page or contact support if this persists.')
  })
})

describe('formatBatchErrors', () => {
  it('returns an empty string for an empty array', () => {
    expect(formatBatchErrors([])).toBe('')
  })

  it('returns the single reason verbatim when only one entry exists', () => {
    expect(formatBatchErrors(['version mismatch'])).toBe('version mismatch')
  })

  it('groups identical reasons with a count prefix', () => {
    const out = formatBatchErrors([
      'version mismatch',
      'version mismatch',
      'version mismatch',
      'not found',
      'not found',
    ])
    expect(out).toBe('3× version mismatch; 2× not found')
  })

  it('preserves first-occurrence ordering across distinct reasons', () => {
    const out = formatBatchErrors(['a', 'b', 'a'])
    expect(out).toBe('2× a; b')
  })
})

describe('getCrudErrorTitle', () => {
  it('returns the fallback title for non-axios errors with no detail', () => {
    expect(getCrudErrorTitle(new Error('boom'), 'Failed to save project').title).toBe(
      'Failed to save project',
    )
  })

  it('returns "Permission denied" for 403 even if a structured AUTH detail is present', () => {
    // 403 short-circuits before the structured-category switch so the
    // operator sees "denied" not "Authentication failed".
    const error = makeAxiosError(403, { error_detail: makeDetail(ErrorCategory.AUTH) })
    expect(getCrudErrorTitle(error, 'Failed').title).toBe('Permission denied')
  })

  it('maps each ErrorCategory to its expected title', () => {
    const cases: Array<[ErrorCategory, string]> = [
      [ErrorCategory.AUTH, 'Authentication failed'],
      [ErrorCategory.VALIDATION, 'Validation failed'],
      [ErrorCategory.CONFLICT, 'Resource conflict'],
      [ErrorCategory.RATE_LIMIT, 'Rate limit reached'],
      [ErrorCategory.NOT_FOUND, 'Not found'],
      [ErrorCategory.BUDGET_EXHAUSTED, 'Budget exhausted'],
      [ErrorCategory.PROVIDER_ERROR, 'Provider error'],
    ]
    for (const [category, expected] of cases) {
      const error = makeAxiosError(400, { error_detail: makeDetail(category) })
      expect(getCrudErrorTitle(error, 'fallback').title).toBe(expected)
    }
  })

  it('falls through ErrorCategory.INTERNAL to the HTTP-status / fallback branch', () => {
    // 500 INTERNAL detail with no special status mapping should land on
    // the caller-supplied fallback string, not a category-derived title.
    const error = makeAxiosError(500, { error_detail: makeDetail(ErrorCategory.INTERNAL) })
    expect(getCrudErrorTitle(error, 'Failed to save').title).toBe('Failed to save')
  })

  it('uses HTTP-status fallback titles when no structured detail is present', () => {
    const cases: Array<[number, string]> = [
      [401, 'Authentication failed'],
      [404, 'Not found'],
      [409, 'Resource conflict'],
      [422, 'Validation failed'],
      [429, 'Rate limit reached'],
    ]
    for (const [status, expected] of cases) {
      expect(getCrudErrorTitle(makeAxiosError(status), 'fallback').title).toBe(expected)
    }
  })

  it('returns the caller fallback for non-axios errors', () => {
    expect(getCrudErrorTitle('a string', 'Failed to update agent').title).toBe(
      'Failed to update agent',
    )
  })
})

describe('getErrorCode', () => {
  it('returns the structured error_code when present', () => {
    const detail = makeDetail(ErrorCategory.CONFLICT, ErrorCode.DUPLICATE_RECORD)
    expect(getErrorCode(makeAxiosError(409, { error_detail: detail }))).toBe(
      ErrorCode.DUPLICATE_RECORD,
    )
  })

  it('returns null when no structured detail is present', () => {
    expect(getErrorCode(makeAxiosError(500))).toBeNull()
    expect(getErrorCode(new Error('boom'))).toBeNull()
  })
})

describe('getErrorDetail', () => {
  it('returns null for non-axios error', () => {
    expect(getErrorDetail(new Error('test'))).toBeNull()
  })

  it('returns null when no error_detail in response', () => {
    const error = makeAxiosError(400, { error: 'bad' })
    expect(getErrorDetail(error)).toBeNull()
  })

  it('returns error_detail when present', () => {
    const detail: ErrorDetail = {
      detail: 'Not found',
      error_code: ErrorCode.RESOURCE_NOT_FOUND,
      error_category: ErrorCategory.NOT_FOUND,
      retryable: false,
      retry_after: null,
      instance: 'req-123',
      title: 'Not Found',
      type: 'https://docs.example.com/errors/not-found',
    }
    const error = makeAxiosError(404, { error_detail: detail })
    expect(getErrorDetail(error)).toEqual(detail)
  })

  it('returns null for network error (no response)', () => {
    const error = makeAxiosError(undefined)
    expect(getErrorDetail(error)).toBeNull()
  })
})
