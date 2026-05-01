/** RFC 9457 structured error types mirroring backend `synthorg.api.errors`. */

export type ErrorCategory =
  | 'auth'
  | 'validation'
  | 'not_found'
  | 'conflict'
  | 'rate_limit'
  | 'budget_exhausted'
  | 'provider_error'
  | 'internal'

export type ErrorCode =
  | 1000 | 1001 | 1002 | 1003 | 1004 | 1005 | 1006 | 1007 | 1008 | 1009
  | 2000 | 2001 | 2002 | 2003 | 2004
  | 3000 | 3001 | 3002 | 3003 | 3004 | 3005 | 3006 | 3007 | 3008 | 3009 | 3010 | 3011 | 3012
  | 4000 | 4001 | 4002 | 4003 | 4004 | 4005 | 4006 | 4007
  | 5000 | 5001 | 5002
  | 6000 | 6001 | 6002 | 6003 | 6004
  | 7000 | 7001 | 7002 | 7003 | 7004 | 7005 | 7006 | 7007 | 7008 | 7009
  | 8000 | 8001 | 8002 | 8003 | 8004 | 8005 | 8006 | 8007 | 8008

export interface ErrorDetail {
  detail: string
  error_code: ErrorCode
  error_category: ErrorCategory
  retryable: boolean
  retry_after: number | null
  instance: string
  title: string
  type: string
}

/**
 * Named constants for ``error_detail.error_code`` values that the
 * dashboard pattern-matches on. Mirrors selected entries from the
 * backend ``synthorg.core.error_taxonomy.ErrorCode`` enum. Add new
 * entries here lazily; only codes the UI actually discriminates on
 * need a name; the rest are still typed via the ``ErrorCode`` union
 * above.
 */
export const ERROR_CODE_PROVIDER_TIER_COVERAGE_INSUFFICIENT: ErrorCode = 2004

/** 401 emitted when the request had no session cookie / bearer token. */
export const ERROR_CODE_SESSION_NO_TOKEN: ErrorCode = 1008

/** 401 emitted when the supplied session cookie / JWT was rejected as expired or invalid. */
export const ERROR_CODE_SESSION_EXPIRED: ErrorCode = 1009
