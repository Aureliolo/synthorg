import fc from 'fast-check'
import type { AxiosResponse } from 'axios'
import { unwrap, unwrapPaginated, ApiRequestError } from '@/api/client'
import { ErrorCategory, ErrorCode } from '@/api/types/errors'
import type { ApiResponse, PaginatedResponse } from '@/api/types/http'

function mockResponse<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} as AxiosResponse['config'] }
}

describe('client property tests', () => {
  it('unwrap returns data for any success envelope', () => {
    fc.assert(
      fc.property(fc.anything(), (payload) => {
        // Exclude null/undefined since those are treated as error
        if (payload === null || payload === undefined) return
        const response = mockResponse<ApiResponse<unknown>>({
          data: payload,
          error: null,
          error_detail: null,
          success: true,
        })
        expect(unwrap(response)).toEqual(payload)
      }),
    )
  })

  it('unwrap throws ApiRequestError for any error envelope', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), (errorMsg) => {
        const response = mockResponse<ApiResponse<null>>({
          data: null,
          error: errorMsg,
          error_detail: {
            detail: errorMsg,
            error_code: ErrorCode.INTERNAL_ERROR,
            error_category: ErrorCategory.INTERNAL,
            retryable: false,
            retry_after: null,
            instance: 'test',
            title: 'Error',
            type: 'about:blank',
          },
          success: false,
        })
        expect(() => unwrap(response)).toThrow(ApiRequestError)
      }),
    )
  })

  it('unwrapPaginated returns correct structure for valid paginated envelopes', () => {
    fc.assert(
      fc.property(
        fc.array(fc.record({ id: fc.string() })),
        fc.integer({ min: 1, max: 200 }),
        (items, limit) => {
          const response = mockResponse<PaginatedResponse<{ id: string }>>({
            data: items,
            error: null,
            error_detail: null,
            success: true,
            pagination: { limit, next_cursor: null, has_more: false },
            degraded_sources: [],
          })
          const result = unwrapPaginated(response)
          expect(result.data).toEqual(items)
          expect(result.limit).toBe(limit)
          // Terminal page: cursor fields must round-trip from the
          // envelope into the unwrapped shape so cursor regressions
          // (e.g. mapping ``next_cursor`` to the wrong key) surface.
          expect(result.nextCursor).toBeNull()
          expect(result.hasMore).toBe(false)
        },
      ),
    )
  })

  it('unwrapPaginated preserves cursor metadata for intermediate pages', () => {
    fc.assert(
      fc.property(
        fc.array(fc.record({ id: fc.string() }), { minLength: 1, maxLength: 10 }),
        fc.string({ minLength: 4, maxLength: 32 }),
        (items, nextCursor) => {
          const response = mockResponse<PaginatedResponse<{ id: string }>>({
            data: items,
            error: null,
            error_detail: null,
            success: true,
            pagination: {
              limit: items.length,
              next_cursor: nextCursor,
              has_more: true,
            },
            degraded_sources: [],
          })
          const result = unwrapPaginated(response)
          expect(result.nextCursor).toBe(nextCursor)
          expect(result.hasMore).toBe(true)
        },
      ),
    )
  })
})
