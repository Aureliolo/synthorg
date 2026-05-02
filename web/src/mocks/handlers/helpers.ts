import type { ErrorDetail } from '@/api/types/errors'
import type {
  ApiResponse,
  PaginatedResponse,
  PaginationMeta,
} from '@/api/types/http'
import type { PaginatedResult } from '@/api/client'

/** Build a successful ApiResponse<T> envelope for MSW handlers. */
export function apiSuccess<T>(data: T): ApiResponse<T> {
  return { data, error: null, error_detail: null, success: true }
}

/** Default ErrorDetail used by both apiError and apiPaginatedError. */
function buildDefaultErrorDetail(
  error: string,
  overrides?: Partial<ErrorDetail>,
): ErrorDetail {
  return {
    detail: error,
    error_code: 1000,
    error_category: 'internal',
    retryable: false,
    retry_after: null,
    instance: '/storybook',
    title: 'Error',
    type: 'about:blank',
    ...overrides,
  }
}

/** Build a failed ApiResponse envelope for MSW handlers. */
export function apiError(
  error: string,
  overrides?: Partial<ErrorDetail>,
): ApiResponse<never> {
  return {
    data: null,
    error,
    error_detail: buildDefaultErrorDetail(error, overrides),
    success: false,
  }
}

/**
 * Build a uniform validation-error envelope for MSW handlers.
 *
 * Centralises the "missing or invalid required field" copy so mock
 * handlers don't drift from each other (some used "Missing required
 * fields", others "Field 'name' is required", others "Fields 'a' and
 * 'b' are required" -- the inconsistency leaked into test
 * expectations and could mask real backend divergence). Pass the
 * required field names; the helper formats them into a stable
 * "Validation error: a, b are required." sentence.
 */
export function buildValidationError(
  fields: readonly string[],
  overrides?: Partial<ErrorDetail>,
): ApiResponse<never> {
  if (fields.length === 0) {
    return apiError('Validation error: required field is missing.', {
      error_code: 4001,
      error_category: 'validation',
      title: 'Validation error',
      ...overrides,
    })
  }
  const formatted = fields.length === 1
    ? `${fields[0]} is required`
    : `${fields.slice(0, -1).join(', ')} and ${fields[fields.length - 1]} are required`
  return apiError(`Validation error: ${formatted}.`, {
    error_code: 4001,
    error_category: 'validation',
    title: 'Validation error',
    ...overrides,
  })
}

/** Build a failed paginated envelope (data=null, pagination=null). */
export function apiPaginatedError(
  error: string,
  overrides?: Partial<ErrorDetail>,
): PaginatedResponse<never> {
  return {
    data: null,
    error,
    error_detail: buildDefaultErrorDetail(error, overrides),
    pagination: null,
    success: false,
  }
}

type AwaitedReturn<Fn> = Fn extends (...args: never[]) => Promise<infer R> ? R : never

/**
 * Build an ApiResponse envelope typed to an endpoint function's return type.
 *
 * Binds the handler's payload to the same shape the production store sees
 * when the endpoint resolves successfully. If the endpoint module renames
 * or reshapes a return type, every handler using `successFor<typeof fn>`
 * turns red in TypeScript.
 */
export function successFor<Fn extends (...args: never[]) => Promise<unknown>>(
  data: AwaitedReturn<Fn>,
): ApiResponse<AwaitedReturn<Fn>> {
  return apiSuccess(data)
}

/** Null-data ApiResponse envelope for endpoints that return `void`. */
export function voidSuccess(): ApiResponse<null> {
  return apiSuccess(null)
}

/**
 * Build a PaginatedResponse envelope from the unwrapped `PaginatedResult`
 * shape an endpoint function returns.
 *
 * Accepts a `{ data, total, offset, limit }` tuple (the store-facing shape)
 * and lifts it into the wire envelope with a nested `pagination` object.
 */
export function paginatedFor<
  Fn extends (...args: never[]) => Promise<PaginatedResult<unknown>>,
>(
  result: AwaitedReturn<Fn>,
): PaginatedResponse<
  AwaitedReturn<Fn> extends PaginatedResult<infer Item> ? Item : never
> {
  type Item = AwaitedReturn<Fn> extends PaginatedResult<infer I> ? I : never
  const pagination: PaginationMeta = {
    limit: result.limit,
    next_cursor: result.nextCursor,
    has_more: result.hasMore,
  }
  return {
    data: result.data as Item[],
    error: null,
    error_detail: null,
    pagination,
    success: true,
  }
}

/** Build an empty paginated result with default offset/limit. */
export function emptyPage<T>(limit = 200): PaginatedResult<T> {
  return {
    data: [],
    limit,
    nextCursor: null,
    hasMore: false,
    pagination: {
      limit,
      next_cursor: null,
      has_more: false,
    },
  }
}
