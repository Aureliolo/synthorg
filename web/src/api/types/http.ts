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
  | { data: T[]; error: null; error_detail: null; success: true; pagination: PaginationMeta; degraded_sources?: readonly string[] }
  | { data: null; error: string | null; error_detail: ErrorDetail | null; success: false; pagination: null; degraded_sources?: readonly string[] }

export interface PaginationParams {
  /** Opaque pagination cursor from the previous page response. */
  cursor?: string | null
  limit?: number
}
