/** Response envelopes and pagination helpers. */

import type { ErrorDetail } from './errors'

/** Discriminated API response envelope. */
export type ApiResponse<T> =
  | { data: T; error: null; error_detail: null; success: true }
  | { data: null; error: string | null; error_detail: ErrorDetail | null; success: false }

export interface PaginationMeta {
  /** Maximum items per page. */
  limit: number
  /** Opaque cursor for the next page; null on the final page. */
  next_cursor: string | null
  /** Whether more items follow the current page. */
  has_more: boolean
}

/** Discriminated paginated response envelope. */
export type PaginatedResponse<T> =
  | { data: T[]; error: null; error_detail: null; success: true; pagination: PaginationMeta; degraded_sources: readonly string[] }
  // Backend's ``ApiResponse[None]`` does not include a ``pagination``
  // or ``degraded_sources`` field on error responses, so both are
  // omitted here; the ``success: false`` discriminant is what the
  // client checks before touching pagination.
  | { data: null; error: string | null; error_detail: ErrorDetail | null; success: false; pagination?: null }

export interface PaginationParams {
  /** Opaque pagination cursor from the previous page response. */
  cursor?: string | null
  limit?: number
}
