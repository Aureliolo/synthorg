import { describe, it, expect } from 'vitest'
import fc from 'fast-check'
import { unwrap, unwrapPaginated } from '@/api/client'
import type { ApiResponse, PaginatedResponse } from '@/api/types'
import type { AxiosResponse } from 'axios'

function mockResponse<T>(data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as AxiosResponse['config'],
  }
}

describe('unwrap (property-based)', () => {
  it('returns data when success is true and data is present', () => {
    fc.assert(
      fc.property(fc.anything().filter((v) => v !== null && v !== undefined), (data) => {
        const response = mockResponse({ data, error: null, success: true as const })
        const result = unwrap(response)
        expect(result).toEqual(data)
      }),
    )
  })

  it('throws when success is false', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (errorMsg) => {
        const response = mockResponse({ data: null, error: errorMsg, success: false as const })
        expect(() => unwrap(response)).toThrow(errorMsg)
      }),
    )
  })

  it('throws "Unknown API error" when success is false and error is null', () => {
    // Intentionally malformed: success=false with error=null violates discriminated union
    const response = mockResponse({ data: null, error: null, success: false as const }) as unknown as AxiosResponse<ApiResponse<null>>
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })

  it('throws when success is true but data is null', () => {
    // Intentionally malformed: success=true with data=null violates discriminated union
    const response = mockResponse({ data: null, error: null, success: true as const }) as unknown as AxiosResponse<ApiResponse<null>>
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })

  it('throws when success is true but data is undefined', () => {
    // Intentionally malformed: success=true with data=undefined violates discriminated union
    const response = mockResponse({ data: undefined, error: null, success: true as const }) as unknown as AxiosResponse<ApiResponse<undefined>>
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })

  it('either returns data or throws Error on arbitrary envelope shapes', () => {
    fc.assert(
      fc.property(fc.anything(), (body) => {
        const response = mockResponse(body)
        try {
          const result = unwrap(response as unknown as AxiosResponse<ApiResponse<unknown>>)
          // If it didn't throw, we got a value back — that's fine
          expect(result).toBeDefined()
        } catch (err) {
          // Must throw a controlled Error, not crash with a TypeError
          expect(err).toBeInstanceOf(Error)
          expect(err).not.toBeInstanceOf(TypeError)
        }
      }),
    )
  })
})

describe('unwrapPaginated (property-based)', () => {
  it('returns data and pagination for valid paginated responses', () => {
    fc.assert(
      fc.property(
        fc.array(fc.anything(), { maxLength: 20 }),
        fc.nat(),
        fc.nat(),
        fc.integer({ min: 1, max: 200 }),
        (data, total, offset, limit) => {
          const response = mockResponse({
            data,
            error: null,
            success: true as const,
            pagination: { total, offset, limit },
          })
          const result = unwrapPaginated(response)
          expect(result.data).toEqual(data)
          expect(result.total).toBe(total)
          expect(result.offset).toBe(offset)
          expect(result.limit).toBe(limit)
        },
      ),
    )
  })

  it('throws when success is false', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (errorMsg) => {
        const response = mockResponse({
          data: null,
          error: errorMsg,
          success: false as const,
          pagination: null,
        })
        expect(() => unwrapPaginated(response)).toThrow(errorMsg)
      }),
    )
  })

  it('throws "Unknown API error" when success is false and error is null', () => {
    // Intentionally malformed: success=false with error=null violates discriminated union
    const response = mockResponse({
      data: null,
      error: null,
      success: false as const,
      pagination: null,
    }) as unknown as AxiosResponse<PaginatedResponse<unknown>>
    expect(() => unwrapPaginated(response)).toThrow('Unknown API error')
  })

  it('throws when success is true but pagination is missing', () => {
    fc.assert(
      fc.property(fc.array(fc.anything(), { maxLength: 10 }), (data) => {
        // Intentionally malformed: success=true with pagination=null violates discriminated union
        const response = mockResponse({
          data,
          error: null,
          success: true as const,
          pagination: null,
        }) as unknown as AxiosResponse<PaginatedResponse<unknown>>
        expect(() => unwrapPaginated(response)).toThrow('Unexpected API response format')
      }),
    )
  })

  it('throws when success is true but data is not an array', () => {
    fc.assert(
      fc.property(
        fc.anything().filter((v) => !Array.isArray(v)),
        (data) => {
          // Intentionally malformed: non-array data violates discriminated union
          const response = mockResponse({
            data,
            error: null,
            success: true as const,
            pagination: { total: 0, offset: 0, limit: 50 },
          }) as unknown as AxiosResponse<PaginatedResponse<unknown>>
          expect(() => unwrapPaginated(response)).toThrow('Unexpected API response format')
        },
      ),
    )
  })

  it('either returns valid paginated result or throws Error on arbitrary input', () => {
    fc.assert(
      fc.property(fc.anything(), (body) => {
        const response = mockResponse(body)
        try {
          const result = unwrapPaginated(response as unknown as AxiosResponse<PaginatedResponse<unknown>>)
          // If it didn't throw, we got a valid structure
          expect(Array.isArray(result.data)).toBe(true)
          expect(typeof result.total).toBe('number')
          expect(typeof result.offset).toBe('number')
          expect(typeof result.limit).toBe('number')
        } catch (err) {
          expect(err).toBeInstanceOf(Error)
          expect(err).not.toBeInstanceOf(TypeError)
        }
      }),
    )
  })
})
