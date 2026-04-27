/**
 * CodSpeed bench for `unwrapPaginated()`.
 *
 * Called on every list-fetch response. Exercises the validation path
 * + envelope unwrap + nested pagination object copy. A regression here
 * affects every list page in the dashboard.
 */
import { AxiosHeaders, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { bench, describe } from 'vitest'

import { unwrap, unwrapPaginated } from '@/api/client'
import type { ApiResponse, PaginatedResponse } from '@/api/types/http'

// ``AxiosResponse.config`` is a required ``InternalAxiosRequestConfig``;
// we construct one valid instance per file so each ``makeXxxResponse``
// returns a fully-typed value rather than relying on ``as never`` casts
// that would silently break under a future axios type tightening.
const BENCH_REQUEST_CONFIG: InternalAxiosRequestConfig = {
  url: '/bench',
  method: 'GET',
  headers: new AxiosHeaders(),
}

interface Row {
  id: string
  name: string
  status: string
  count: number
}

function makeRow(idx: number): Row {
  return {
    id: `row-${idx.toString().padStart(4, '0')}`,
    name: `Row ${idx}`,
    status: idx % 3 === 0 ? 'active' : idx % 3 === 1 ? 'pending' : 'completed',
    count: 100 + idx * 7,
  }
}

const ROWS_50 = Array.from({ length: 50 }, (_, i) => makeRow(i))
const ROWS_500 = Array.from({ length: 500 }, (_, i) => makeRow(i))

function makePaginatedResponse<T>(rows: T[]): AxiosResponse<PaginatedResponse<T>> {
  return {
    data: {
      data: rows,
      error: null,
      error_detail: null,
      success: true,
      pagination: {
        limit: rows.length,
        next_cursor: 'opaque-cursor-deadbeef',
        has_more: true,
        total: rows.length * 4,
        offset: 0,
      },
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: BENCH_REQUEST_CONFIG,
  }
}

function makeApiResponse<T>(payload: T): AxiosResponse<ApiResponse<T>> {
  return {
    data: {
      data: payload,
      error: null,
      error_detail: null,
      success: true,
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: BENCH_REQUEST_CONFIG,
  }
}

const PAGINATED_50 = makePaginatedResponse(ROWS_50)
const PAGINATED_500 = makePaginatedResponse(ROWS_500)
const SINGLE = makeApiResponse({ id: 'single', name: 'one' })

describe('api client unwrap', () => {
  bench('unwrapPaginated x100 (50 rows)', () => {
    for (let i = 0; i < 100; i++) {
      unwrapPaginated(PAGINATED_50)
    }
  })

  bench('unwrapPaginated x100 (500 rows)', () => {
    for (let i = 0; i < 100; i++) {
      unwrapPaginated(PAGINATED_500)
    }
  })

  bench('unwrap x500 (single object)', () => {
    for (let i = 0; i < 500; i++) {
      unwrap(SINGLE)
    }
  })
})
