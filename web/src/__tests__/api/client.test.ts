import type { AxiosResponse } from 'axios'
import { http, HttpResponse } from 'msw'
import { vi } from 'vitest'

// Mock dev auth bypass OFF so the 401 interceptor actually fires.
// Must be hoisted before client.ts imports @/utils/dev at module level.
vi.mock('@/utils/dev', () => ({ IS_DEV_AUTH_BYPASS: false }))

import {
  ApiRequestError,
  _settleSessionProbeForTests,
  unwrap,
  unwrapNullable,
  unwrapPaginated,
  unwrapVoid,
  apiClient,
} from '@/api/client'
import { _resetUnauthorizedRedirectGuardForTests } from '@/stores/auth'
import { cookieJar } from '@/cookie-shim'
import { ErrorCategory, ErrorCode, type ErrorDetail } from '@/api/types/errors'
import type { ApiResponse, PaginatedResponse } from '@/api/types/http'
import { server } from '@/test-setup'

/**
 * Session probes the interceptor issued. Counted from the request event rather
 * than a handler override, so a test that replaces the ``/auth/me`` handler
 * still counts the call it made.
 */
let getMeCalls = 0

server.events.on('request:start', ({ request }) => {
  if (new URL(request.url).pathname === '/api/v1/auth/me') getMeCalls += 1
})

beforeEach(async () => {
  // The teardown decision is fire-and-forget, so a probe from the previous
  // test can still be in flight; draining it first is what makes both the
  // counter and the latch below start from a known state rather than from
  // whatever the last test happened to leave running.
  await _settleSessionProbeForTests()
  getMeCalls = 0
  // The auth store latches after one teardown so a burst of 401s bounces to
  // login once. Reset per test rather than per block: every test that drives
  // the interceptor needs the latch open, whichever order they run in.
  _resetUnauthorizedRedirectGuardForTests()
})

/**
 * Build an AxiosResponse fixture. ``data`` is widened to ``unknown`` so
 * tests can pass deliberately malformed payloads (null, strings, etc.)
 * to exercise the unwrap/unwrapPaginated error paths -- those are
 * exactly the cases the helper exists to cover.
 */
function mockResponse<T>(data: unknown): AxiosResponse<T> {
  return {
    data: data as T,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {} as AxiosResponse['config'],
  }
}

const testErrorDetail: ErrorDetail = {
  detail: 'Resource not found',
  error_code: ErrorCode.RESOURCE_NOT_FOUND,
  error_category: ErrorCategory.NOT_FOUND,
  retryable: false,
  retry_after: null,
  instance: 'req-abc',
  title: 'Not Found',
  type: 'https://docs.example.com/errors/not-found',
}

describe('ApiRequestError', () => {
  it('sets name and message', () => {
    const err = new ApiRequestError('test error')
    expect(err.name).toBe('ApiRequestError')
    expect(err.message).toBe('test error')
    expect(err.errorDetail).toBeNull()
  })

  it('carries error detail', () => {
    const err = new ApiRequestError('test', testErrorDetail)
    expect(err.errorDetail).toEqual(testErrorDetail)
  })

  it('is an instance of Error', () => {
    const err = new ApiRequestError('test')
    expect(err).toBeInstanceOf(Error)
  })

  it('correlationId returns null when detail is null', () => {
    const err = new ApiRequestError('test')
    expect(err.correlationId).toBeNull()
  })

  it('correlationId returns instance from error detail', () => {
    const err = new ApiRequestError('test', testErrorDetail)
    expect(err.correlationId).toBe('req-abc')
  })

  it('correlationId returns null for empty instance string', () => {
    const err = new ApiRequestError('test', { ...testErrorDetail, instance: '' })
    expect(err.correlationId).toBeNull()
  })

  it('correlationId returns null for whitespace-only instance', () => {
    const err = new ApiRequestError('test', { ...testErrorDetail, instance: '   ' })
    expect(err.correlationId).toBeNull()
  })

  it('correlationId trims surrounding whitespace', () => {
    const err = new ApiRequestError('test', { ...testErrorDetail, instance: '  req-abc  ' })
    expect(err.correlationId).toBe('req-abc')
  })
})

describe('unwrap', () => {
  it('extracts data from success response', () => {
    const response = mockResponse<ApiResponse<{ id: string }>>({
      data: { id: 'test-1' },
      error: null,
      error_detail: null,
      success: true,
    })
    expect(unwrap(response)).toEqual({ id: 'test-1' })
  })

  it('throws for success:true with data:null', () => {
    const response = mockResponse<ApiResponse<null>>({
      data: null,
      error: null,
      error_detail: null,
      success: true,
    })
    expect(() => unwrap(response)).toThrow(ApiRequestError)
  })

  it('throws ApiRequestError for error response', () => {
    const response = mockResponse<ApiResponse<null>>({
      data: null,
      error: 'Something went wrong',
      error_detail: testErrorDetail,
      success: false,
    })
    expect(() => unwrap(response)).toThrow(ApiRequestError)
    try {
      unwrap(response)
    } catch (err) {
      const caught = err as ApiRequestError
      expect(caught.message).toBe('Something went wrong')
      expect(caught.errorDetail).toEqual(testErrorDetail)
    }
  })

  it('throws for null body', () => {
    const response = mockResponse<ApiResponse<unknown>>(null)
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })

  it('throws for non-object body', () => {
    const response = mockResponse<ApiResponse<unknown>>('not an object')
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })

  it('throws for success=false with null error', () => {
    // Deliberately malformed (null where ``error``/``error_detail`` must
    // be a string / ErrorDetail) to exercise the "unknown error" branch.
    // ``mockResponse``'s ``data`` parameter is widened to ``unknown`` so
    // the malformed literal can be passed directly.
    const response = mockResponse<ApiResponse<null>>({
      data: null,
      error: null,
      error_detail: null,
      success: false,
    })
    expect(() => unwrap(response)).toThrow('Unknown API error')
  })
})

describe('unwrapNullable', () => {
  it('extracts data from a success response', () => {
    const response = mockResponse<ApiResponse<{ id: string } | null>>({
      data: { id: 'test-1' },
      error: null,
      error_detail: null,
      success: true,
    })
    expect(unwrapNullable(response)).toEqual({ id: 'test-1' })
  })

  it('returns null for success:true with data:null (no resource)', () => {
    const response = mockResponse<ApiResponse<{ id: string } | null>>({
      data: null,
      error: null,
      error_detail: null,
      success: true,
    })
    expect(unwrapNullable(response)).toBeNull()
  })

  it('throws ApiRequestError for an error response', () => {
    const response = mockResponse<ApiResponse<{ id: string } | null>>({
      data: null,
      error: 'Something went wrong',
      error_detail: testErrorDetail,
      success: false,
    })
    expect(() => unwrapNullable(response)).toThrow(ApiRequestError)
  })

  it('throws for a null body', () => {
    const response = mockResponse<ApiResponse<unknown>>(null)
    expect(() => unwrapNullable(response)).toThrow('Unknown API error')
  })

  it('throws for success:true with the data key absent (malformed)', () => {
    const response = mockResponse<ApiResponse<{ id: string } | null>>({
      error: null,
      error_detail: null,
      success: true,
    })
    expect(() => unwrapNullable(response)).toThrow('success envelope missing data')
  })
})

describe('unwrapVoid', () => {
  it('does not throw for success response', () => {
    const response = mockResponse<ApiResponse<null>>({
      data: null,
      error: null,
      error_detail: null,
      success: true,
    })
    expect(() => unwrapVoid(response)).not.toThrow()
  })

  it('handles 204 No Content with empty body', () => {
    const response = mockResponse<ApiResponse<null>>('')
    response.status = 204
    response.statusText = 'No Content'
    expect(() => unwrapVoid(response)).not.toThrow()
  })

  it('throws ApiRequestError for error response', () => {
    const response = mockResponse<ApiResponse<null>>({
      data: null,
      error: 'Failed',
      error_detail: testErrorDetail,
      success: false,
    })
    expect(() => unwrapVoid(response)).toThrow(ApiRequestError)
  })
})

describe('unwrapPaginated', () => {
  it('extracts data and pagination from success response', () => {
    const response = mockResponse<PaginatedResponse<{ id: string }>>({
      data: [{ id: 'a' }, { id: 'b' }],
      error: null,
      error_detail: null,
      success: true,
      pagination: { limit: 50, next_cursor: null, has_more: false },
    })
    const result = unwrapPaginated(response)
    expect(result.data).toHaveLength(2)
    expect(result.limit).toBe(50)
  })

  it('throws ApiRequestError for error response', () => {
    const response = mockResponse<PaginatedResponse<unknown>>({
      data: null,
      error: 'Error occurred',
      error_detail: testErrorDetail,
      success: false,
      pagination: null,
    })
    expect(() => unwrapPaginated(response)).toThrow(ApiRequestError)
  })

  it('throws for missing pagination', () => {
    const response = mockResponse<PaginatedResponse<unknown>>({
      data: [],
      error: null,
      error_detail: null,
      success: true,
      pagination: null,
    })
    expect(() => unwrapPaginated(response)).toThrow('Unexpected API response format')
  })

  it('throws for non-array data', () => {
    const response = mockResponse<PaginatedResponse<unknown>>({
      data: 'not-array',
      error: null,
      error_detail: null,
      success: true,
      pagination: { limit: 50, next_cursor: null, has_more: false },
    })
    expect(() => unwrapPaginated(response)).toThrow('Unexpected API response format')
  })
})

/** Extract the fulfilled handler from the first request interceptor -- throws if not found. */
function getRequestInterceptor(): (config: Record<string, unknown>) => Record<string, unknown> {
  // ``handlers`` is an undocumented axios internal exposed via the
  // module augmentation in ``__tests__/_types/axios-internal.d.ts``.
  const handlers = apiClient.interceptors.request.handlers
  const fulfilled = handlers?.[0]?.fulfilled
  if (!fulfilled) throw new Error('Request interceptor not found -- Axios internals may have changed')
  // The augmented type is ``InternalAxiosRequestConfig`` which insists on
  // ``headers``. Tests pass minimal records, so we widen via ``unknown``.
  return fulfilled as unknown as (
    config: Record<string, unknown>,
  ) => Record<string, unknown>
}

// Spy on getCsrfToken rather than setting `document.cookie` -- the
// cookie shim covers the synchronous read fast-path, but mocking the
// reader keeps the test's scope precisely on the interceptor's
// behaviour. The cookie-parsing logic inside `csrf.ts` itself is
// covered directly by `__tests__/utils/csrf.test.ts`.
// Compile-time guard: if `@/utils/csrf` gains new exports, the type
// import below will error until this mock is extended.
import type * as CsrfModule from '@/utils/csrf'
type CsrfExports = keyof typeof CsrfModule
const _csrfMockExports: Record<CsrfExports, unknown> = {
  getCsrfToken: true,
  parseCsrfTokenFromCookieString: true,
}
void _csrfMockExports
vi.mock('@/utils/csrf', () => ({
  getCsrfToken: vi.fn(),
  parseCsrfTokenFromCookieString: vi.fn(),
}))

describe('apiClient request interceptor (CSRF)', () => {
  let csrfMock: ReturnType<typeof vi.fn>

  beforeAll(async () => {
    const mod = await import('@/utils/csrf')
    csrfMock = vi.mocked(mod.getCsrfToken) as ReturnType<typeof vi.fn>
  })

  beforeEach(() => {
    csrfMock.mockReturnValue('test-csrf-token')
  })

  afterEach(() => {
    csrfMock.mockReset()
  })

  it('attaches CSRF token on POST requests when cookie present', () => {
    const fulfilled = getRequestInterceptor()
    const result = fulfilled({ method: 'post', headers: {} }) as { headers: Record<string, string> }
    expect(result.headers['X-CSRF-Token']).toBe('test-csrf-token')
  })

  it('attaches CSRF token on PUT requests when cookie present', () => {
    const fulfilled = getRequestInterceptor()
    const result = fulfilled({ method: 'put', headers: {} }) as { headers: Record<string, string> }
    expect(result.headers['X-CSRF-Token']).toBe('test-csrf-token')
  })

  it('attaches CSRF token on PATCH requests when cookie present', () => {
    const fulfilled = getRequestInterceptor()
    const result = fulfilled({ method: 'patch', headers: {} }) as { headers: Record<string, string> }
    expect(result.headers['X-CSRF-Token']).toBe('test-csrf-token')
  })

  it('attaches CSRF token on DELETE requests when cookie present', () => {
    const fulfilled = getRequestInterceptor()
    const result = fulfilled({ method: 'delete', headers: {} }) as { headers: Record<string, string> }
    expect(result.headers['X-CSRF-Token']).toBe('test-csrf-token')
  })

  it('does not attach CSRF token on GET requests', () => {
    const fulfilled = getRequestInterceptor()
    const result = fulfilled({ method: 'get', headers: {} }) as { headers: Record<string, string> }
    expect(result.headers['X-CSRF-Token']).toBeUndefined()
  })

  it('does not attach CSRF token when cookie is absent', () => {
    // The request interceptor reads the csrf_token cookie through the cookie
    // shim. The global setup seeds that cookie for the mutating-request tests,
    // so to exercise the truly-absent path we clear it from the jar here (the
    // global afterEach re-seeds it for the next test). The getCsrfToken mock is
    // ineffective because backend-sourced stores load `@/api/client` -- and its
    // interceptor's live csrf binding -- before this file's vi.mock applies.
    csrfMock.mockReturnValue(null)
    delete cookieJar['csrf_token']
    const fulfilled = getRequestInterceptor()
    const result = fulfilled({ method: 'post', headers: {} }) as { headers: Record<string, string> }
    expect(result.headers['X-CSRF-Token']).toBeUndefined()
  })
})

describe('apiClient config', () => {
  it('has withCredentials enabled', () => {
    expect(apiClient.defaults.withCredentials).toBe(true)
  })
})

describe('apiClient 401 response interceptor', () => {
  it('passes through non-401 errors unchanged', async () => {
    const error = new (await import('axios')).AxiosError(
      'Server Error',
      'ERR_BAD_RESPONSE',
      undefined,
      undefined,
      { status: 500, data: {}, headers: {}, statusText: 'Error', config: {} as AxiosResponse['config'] } as AxiosResponse,
    )

    await expect(apiClient.interceptors.response.handlers?.[0]?.rejected?.(error)).rejects.toBeDefined()
  })

  it('flips auth to unauthenticated on 401 (integration)', async () => {
    const { AxiosError } = await import('axios')
    const { useAuthStore } = await import('@/stores/auth')

    // Seed an authenticated session so we can observe the flip.
    useAuthStore.setState({
      authStatus: 'authenticated',
      user: {
        id: '1',
        username: 'admin',
        role: 'ceo',
        must_change_password: false,
        org_roles: [],
        scoped_departments: [],
      },
      loading: false,
    })

    const error = new AxiosError(
      'Unauthorized',
      'ERR_BAD_RESPONSE',
      undefined,
      undefined,
      {
        status: 401,
        data: {},
        headers: {},
        statusText: 'Unauthorized',
        config: {} as AxiosResponse['config'],
      } as AxiosResponse,
    )

    // The interceptor re-throws after triggering handleUnauthorized.
    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(error),
    ).rejects.toBeDefined()

    // Dynamic import chain inside the interceptor resolves on a microtask;
    // wait for the flip rather than assuming synchronous execution.
    await vi.waitFor(() => {
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
      expect(useAuthStore.getState().user).toBeNull()
    })
  })
})

describe('a 401 is confirmed before the session is torn down', () => {
  async function unauthorized(url: string) {
    const { AxiosError } = await import('axios')
    return new AxiosError('Unauthorized', 'ERR_BAD_RESPONSE', { url, headers: {} } as never, undefined, {
      status: 401,
      data: {},
      headers: {},
      statusText: 'Unauthorized',
      config: {} as AxiosResponse['config'],
    } as AxiosResponse)
  }

  async function signedIn() {
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore.setState({
      authStatus: 'authenticated',
      user: {
        id: '1',
        username: 'admin',
        role: 'ceo',
        must_change_password: false,
        org_roles: [],
        scoped_departments: [],
      },
      loading: false,
    })
    return useAuthStore
  }

  it('keeps a session the backend still recognises', async () => {
    // A single 401 is not proof of expiry: the very next call can return 200
    // on the same cookie.
    const useAuthStore = await signedIn()

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(await unauthorized('/tasks')),
    ).rejects.toBeDefined()

    // The probe resolves on a later tick, so a passing assertion here has to
    // outlive it rather than beat it.
    await vi.waitFor(() => {
      expect(getMeCalls).toBeGreaterThan(0)
    })
    expect(useAuthStore.getState().authStatus).toBe('authenticated')
  })

  it('tears down when the backend confirms the session is gone', async () => {
    const useAuthStore = await signedIn()
    server.use(
      http.get('/api/v1/auth/me', () => new HttpResponse(null, { status: 401 })),
    )

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(await unauthorized('/tasks')),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
    })
  })

  it('keeps the session when the probe cannot reach the backend', async () => {
    // The probe runs the same wire as the 401 that prompted it, in the same
    // instability window. A probe that never got an answer is not an answer.
    const useAuthStore = await signedIn()
    server.use(http.get('/api/v1/auth/me', () => HttpResponse.error()))

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(await unauthorized('/tasks')),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(getMeCalls).toBeGreaterThan(0)
    })
    await _settleSessionProbeForTests()
    expect(useAuthStore.getState().authStatus).toBe('authenticated')
  })

  it('keeps the session when the probe itself errors', async () => {
    // A 500 on /auth/me says the backend is unwell, not that the cookie died.
    const useAuthStore = await signedIn()
    server.use(
      http.get('/api/v1/auth/me', () => new HttpResponse(null, { status: 500 })),
    )

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(await unauthorized('/tasks')),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(getMeCalls).toBeGreaterThan(0)
    })
    await _settleSessionProbeForTests()
    expect(useAuthStore.getState().authStatus).toBe('authenticated')
  })

  it('matches an unauthenticated path carrying a query string', async () => {
    // The path is what decides, so a caller appending a query must not start
    // asking an unauthenticated caller to prove a session it never had.
    const useAuthStore = await signedIn()
    const before = getMeCalls

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(
        await unauthorized('/auth/login?next=%2Fplans'),
      ),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
    })
    expect(getMeCalls).toBe(before)
  })

  it('does not probe when the probe itself is what returned 401', async () => {
    // Otherwise the answer to "is the session gone" is another request that
    // asks the same question.
    const useAuthStore = await signedIn()
    const before = getMeCalls

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(await unauthorized('/auth/me')),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
    })
    expect(getMeCalls).toBe(before)
  })

  it('does not probe a 401 it cannot attribute to a request', async () => {
    // With no url there is nothing to exempt and nothing to confirm against,
    // so the safe reading is the one that ends the session rather than the
    // one that keeps an expired session alive on a request nobody can name.
    const useAuthStore = await signedIn()
    const before = getMeCalls
    const { AxiosError } = await import('axios')
    const noConfig = new AxiosError('Unauthorized', 'ERR_BAD_RESPONSE', undefined, undefined, {
      status: 401,
      data: {},
      headers: {},
      statusText: 'Unauthorized',
      config: {} as AxiosResponse['config'],
    } as AxiosResponse)

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(noConfig),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
    })
    expect(getMeCalls).toBe(before)
  })

  it('does not probe a rejected login', async () => {
    // An unauthenticated caller has no session to confirm, so the round trip
    // would only ask it to prove something it never had.
    const useAuthStore = await signedIn()
    const before = getMeCalls

    await expect(
      apiClient.interceptors.response.handlers?.[0]?.rejected?.(await unauthorized('/auth/login')),
    ).rejects.toBeDefined()

    await vi.waitFor(() => {
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
    })
    expect(getMeCalls).toBe(before)
  })

  it('shares one probe across a burst of 401s', async () => {
    // A page mounting fires many requests at once; one expiry must not cost
    // one confirmation each.
    await signedIn()
    const before = getMeCalls
    const rejected = apiClient.interceptors.response.handlers?.[0]?.rejected

    await Promise.allSettled(
      ['/tasks', '/agents', '/plans'].map(async (url) => {
        await rejected?.(await unauthorized(url))
      }),
    )

    await vi.waitFor(() => {
      expect(getMeCalls).toBeGreaterThan(before)
    })
    expect(getMeCalls - before).toBe(1)
  })
})
