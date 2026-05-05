import { AxiosError, type AxiosResponse } from 'axios'
import { formatBatchErrors, getErrorMessage, getErrorDetail, isAxiosError } from '@/utils/errors'
import type { ErrorDetail } from '@/api/types/errors'

function makeAxiosError(
  status: number | undefined,
  data?: Record<string, unknown>,
): AxiosError {
  const error = new AxiosError(
    'Request failed',
    status ? 'ERR_BAD_RESPONSE' : 'ERR_NETWORK',
    undefined,
    undefined,
    status
      ? {
          status,
          data,
          headers: {},
          statusText: 'Error',
          config: {} as AxiosResponse['config'],
        } as AxiosResponse
      : undefined,
  )
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

  it('returns a refresh-and-retry message for 409', () => {
    // 409 covers concurrency races, duplicate-resource, and version-
    // mismatch; the refreshed copy avoids implying it is always a
    // concurrency conflict.
    const error = makeAxiosError(409)
    expect(getErrorMessage(error)).toMatch(/refresh/i)
  })

  it('returns validation message for 422 when no detail is present', () => {
    const error = makeAxiosError(422)
    expect(getErrorMessage(error)).toContain('Validation')
  })

  it('surfaces structured error_detail.detail for 422 when data.error is absent', () => {
    const detail: ErrorDetail = {
      detail: 'currency: invalid_code',
      error_code: 5000,
      error_category: 'validation',
      retryable: false,
      retry_after: null,
      instance: 'req-422',
      title: 'Validation Failed',
      type: 'https://docs.example.com/errors/validation',
    }
    const error = makeAxiosError(422, { error_detail: detail })
    expect(getErrorMessage(error)).toBe('currency: invalid_code')
  })

  it('returns rate limit message for 429', () => {
    const error = makeAxiosError(429)
    expect(getErrorMessage(error)).toContain('Too many requests')
  })

  it('returns unavailable message for 503', () => {
    const error = makeAxiosError(503)
    expect(getErrorMessage(error)).toContain('temporarily unavailable')
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
      error_code: 3000,
      error_category: 'not_found',
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
