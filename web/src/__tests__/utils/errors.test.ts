import { describe, it, expect } from 'vitest'
import { getErrorMessage, isAxiosError } from '@/utils/errors'

describe('isAxiosError', () => {
  it('returns false for plain errors', () => {
    expect(isAxiosError(new Error('test'))).toBe(false)
  })

  it('returns true for axios-like errors', () => {
    const axiosError = { isAxiosError: true, response: { status: 400 } }
    expect(isAxiosError(axiosError)).toBe(true)
  })
})

describe('getErrorMessage', () => {
  it('extracts message from Error', () => {
    expect(getErrorMessage(new Error('test message'))).toBe('test message')
  })

  it('returns generic message for unknown errors', () => {
    expect(getErrorMessage(42)).toBe('An unexpected error occurred.')
  })

  it('extracts API error from axios response', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 400,
        data: { error: 'Bad input', success: false, data: null },
      },
    }
    expect(getErrorMessage(axiosError)).toBe('Bad input')
  })

  it('returns status-based message for 401', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 401,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toBe('Authentication required. Please log in.')
  })

  it('returns status-based message for 403', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 403,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toBe('You do not have permission to perform this action.')
  })

  it('returns status-based message for 409', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 409,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toContain('Conflict')
  })

  it('returns status-based message for 422', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 422,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toBe('Validation error. Please check your input.')
  })

  it('returns status-based message for 429', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 429,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toBe('Too many requests. Please try again in a moment.')
  })

  it('returns network error for no response', () => {
    const axiosError = {
      isAxiosError: true,
      response: undefined,
    }
    expect(getErrorMessage(axiosError)).toBe('Network error. Please check your connection.')
  })

  it('returns generic message for 5xx responses (does not leak internals)', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 500,
        data: { error: 'Internal: database pool exhausted at line 42', success: false },
      },
    }
    expect(getErrorMessage(axiosError)).toBe('A server error occurred. Please try again later.')
  })

  it('returns status-based message for 404', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 404,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toBe('The requested resource was not found.')
  })

  it('returns status-based message for 503', () => {
    const axiosError = {
      isAxiosError: true,
      response: {
        status: 503,
        data: {},
      },
    }
    expect(getErrorMessage(axiosError)).toBe('Service temporarily unavailable. Please try again later.')
  })

  it('returns generic message for JSON-like Error.message', () => {
    const err = new Error('{"stack":"at Object.<anonymous> (/app/server.js:15:7)"}')
    expect(getErrorMessage(err)).toBe('An unexpected error occurred.')
  })
})
