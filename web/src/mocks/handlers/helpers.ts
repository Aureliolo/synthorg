import {
  ErrorCategory,
  ErrorCode,
  type ErrorDetail,
} from '@/api/types/errors'
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
    // INTERNAL_ERROR (8000) is the right pairing for the INTERNAL
    // category; the prior UNAUTHORIZED (1000, auth band) was a
    // category/code mismatch that violated the band invariant
    // documented in src/synthorg/core/error_taxonomy.py.
    error_code: ErrorCode.INTERNAL_ERROR,
    error_category: ErrorCategory.INTERNAL,
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
      // REQUEST_VALIDATION_ERROR (2001) sits in the validation band;
      // the prior DUPLICATE_RECORD (4001, conflict band) was a
      // category/code mismatch that would have failed the backend's
      // band-prefix invariant.
      error_code: ErrorCode.REQUEST_VALIDATION_ERROR,
      error_category: ErrorCategory.VALIDATION,
      title: 'Validation error',
      ...overrides,
    })
  }
  const formatted = fields.length === 1
    ? `${fields[0]} is required`
    : `${fields.slice(0, -1).join(', ')} and ${fields[fields.length - 1]} are required`
  return apiError(`Validation error: ${formatted}.`, {
    error_code: ErrorCode.REQUEST_VALIDATION_ERROR,
    error_category: ErrorCategory.VALIDATION,
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

/**
 * Item type extracted from an endpoint function's flattened return
 * type. Endpoints that walk pages via ``paginateAll`` return either
 * ``T[]`` (most lists), ``readonly T[]`` (frozen lists), or
 * ``Record<string, T>`` (provider-style keyed maps) -- never a
 * ``PaginatedResult``. The ``string extends keyof R`` guard only
 * matches a true index signature so a misuse against a shaped object
 * (e.g. a single-resource endpoint) resolves to ``never`` and the
 * call fails to type-check.
 */
type PaginatedItem<R> = R extends readonly (infer V)[]
  ? V
  : R extends Record<string, infer V>
    ? string extends keyof R
      ? V
      : never
    : never

/**
 * Build a ``PaginatedResponse<T>`` envelope tied to an endpoint
 * function's flattened return type. Use this for handlers whose
 * upstream endpoint walks pages via ``paginateAll`` and returns a
 * flattened array or string-keyed map (so ``paginatedFor`` is not
 * applicable -- it requires the endpoint to return
 * ``PaginatedResult<T>`` directly).
 *
 * The item type is inferred from ``Awaited<ReturnType<Fn>>``:
 *
 *   - ``Promise<T[]>`` / ``Promise<readonly T[]>`` -> ``T``
 *   - ``Promise<Record<string, T>>`` -> ``T``
 *   - any other shape -> ``never`` (call fails to type-check)
 *
 * If the endpoint is renamed or its item type drifts, every handler
 * call site turns red in TypeScript -- the envelope stays in lockstep
 * with the contract.
 */
export function paginatedEnvelopeFor<
  Fn extends (...args: never[]) => Promise<unknown>,
>(
  items: readonly PaginatedItem<AwaitedReturn<Fn>>[] = [],
  options: {
    limit?: number
    nextCursor?: string | null
    hasMore?: boolean
  } = {},
): PaginatedResponse<PaginatedItem<AwaitedReturn<Fn>>> {
  type Item = PaginatedItem<AwaitedReturn<Fn>>
  return {
    data: items.slice() as Item[],
    error: null,
    error_detail: null,
    pagination: {
      limit: options.limit ?? 200,
      next_cursor: options.nextCursor ?? null,
      has_more: options.hasMore ?? false,
    },
    success: true,
  }
}
