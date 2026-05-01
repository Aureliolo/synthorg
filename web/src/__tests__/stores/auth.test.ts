import { http, HttpResponse } from 'msw'
import {
  _resetUnauthorizedRedirectGuardForTests,
  useAuthStore,
} from '@/stores/auth'
import { apiError, apiSuccess, voidSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { AuthResponse, UserInfoResponse } from '@/api/types/auth'

// Disable dev auth bypass so the store uses the real auth flow.
vi.mock('@/utils/dev', () => ({ IS_DEV_AUTH_BYPASS: false }))

// `handleUnauthorized` dynamically imports `@/stores/websocket` and calls
// `disconnect()`. Without a mock, the dynamic import + `.then().catch()`
// chain outlives the test body and leaks as PROMISEs. Stub the module
// with a stable spy so tests can assert `disconnect` was called.
const mockDisconnect = vi.fn()
vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: {
    getState: () => ({ disconnect: mockDisconnect }),
  },
}))

// Prevent actual navigation in tests. We must supply a valid `href`
// (not an empty string) because the real axios client resolves
// relative URLs against window.location, and the URL() constructor
// rejects an empty base with "Invalid URL".
const originalLocation = window.location
beforeAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      ...originalLocation,
      href: 'http://localhost/dashboard',
      origin: 'http://localhost',
      pathname: '/dashboard',
    },
  })
})
afterAll(() => {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: originalLocation,
  })
})

const mockAuthResponse: AuthResponse = {
  expires_in: 3600,
  must_change_password: false,
}

const mockUser: UserInfoResponse = {
  id: 'user-1',
  username: 'admin',
  role: 'ceo',
  must_change_password: false,
  org_roles: [],
  scoped_departments: [],
}

function resetStore() {
  useAuthStore.setState({
    authStatus: 'unknown',
    user: null,
    loading: false,
  })
  // Reset the one-shot redirect guard so each test exercises a clean
  // burst of `handleUnauthorized()`. Without this, the guard set by
  // a prior test (or a prior call inside the same test) would no-op
  // every subsequent invocation.
  _resetUnauthorizedRedirectGuardForTests()
  window.location.pathname = '/dashboard'
  window.location.href = 'http://localhost/dashboard'
}

describe('auth store', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
  })

  describe('handleUnauthorized', () => {
    it('sets unauthenticated and clears user', () => {
      useAuthStore.setState({ authStatus: 'authenticated', user: mockUser })

      useAuthStore.getState().handleUnauthorized()

      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('tears down the WebSocket transport', async () => {
      useAuthStore.setState({ authStatus: 'authenticated', user: mockUser })

      useAuthStore.getState().handleUnauthorized()

      await vi.waitFor(() => {
        expect(mockDisconnect).toHaveBeenCalled()
      })
    })

    it('redirects to /login when not already there', () => {
      window.location.pathname = '/dashboard'
      useAuthStore.setState({ authStatus: 'authenticated' })
      useAuthStore.getState().handleUnauthorized()
      expect(window.location.href).toBe('/login')
    })

    it('does not redirect when already on /login', () => {
      window.location.pathname = '/login'
      window.location.href = 'http://localhost/login'
      useAuthStore.setState({ authStatus: 'authenticated' })
      useAuthStore.getState().handleUnauthorized()
      expect(window.location.href).toBe('http://localhost/login')
    })

    it('does not redirect when on /setup', () => {
      window.location.pathname = '/setup'
      window.location.href = 'http://localhost/setup'
      useAuthStore.setState({ authStatus: 'authenticated' })
      useAuthStore.getState().handleUnauthorized()
      expect(window.location.href).toBe('http://localhost/setup')
    })
  })

  describe('login', () => {
    it('authenticates and fetches user on success', async () => {
      let capturedBody: unknown = null
      server.use(
        http.post('/api/v1/auth/login', async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json(apiSuccess(mockAuthResponse))
        }),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiSuccess(mockUser)),
        ),
      )

      await useAuthStore.getState().login('admin', 'pass')

      expect(capturedBody).toEqual({ username: 'admin', password: 'pass' })
      expect(useAuthStore.getState().authStatus).toBe('authenticated')
      expect(useAuthStore.getState().user).toEqual(mockUser)
      expect(useAuthStore.getState().loading).toBe(false)
    })

    it('clears auth when login API fails', async () => {
      server.use(
        http.post('/api/v1/auth/login', () =>
          HttpResponse.json(apiError('bad credentials')),
        ),
      )

      await expect(
        useAuthStore.getState().login('admin', 'wrong'),
      ).rejects.toThrow(/bad credentials/)
      expect(useAuthStore.getState().loading).toBe(false)
    })
  })

  describe('setup', () => {
    it('calls setup API and fetches user', async () => {
      let capturedBody: unknown = null
      server.use(
        http.post('/api/v1/auth/setup', async ({ request }) => {
          capturedBody = await request.json()
          return HttpResponse.json(apiSuccess(mockAuthResponse))
        }),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiSuccess(mockUser)),
        ),
      )

      await useAuthStore.getState().setup('admin', 'pass')

      expect(capturedBody).toEqual({ username: 'admin', password: 'pass' })
      expect(useAuthStore.getState().authStatus).toBe('authenticated')
      expect(useAuthStore.getState().user).toEqual(mockUser)
    })
  })

  describe('fetchUser', () => {
    it('skips when already authenticated with user loaded', async () => {
      let calls = 0
      server.use(
        http.get('/api/v1/auth/me', () => {
          calls += 1
          return HttpResponse.json(apiSuccess(mockUser))
        }),
      )
      useAuthStore.setState({ authStatus: 'authenticated', user: mockUser })

      await useAuthStore.getState().fetchUser()

      expect(calls).toBe(0)
    })

    it('sets user and authenticated on success', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiSuccess(mockUser)),
        ),
      )
      useAuthStore.setState({ authStatus: 'unknown' })

      await useAuthStore.getState().fetchUser()

      expect(useAuthStore.getState().user).toEqual(mockUser)
      expect(useAuthStore.getState().authStatus).toBe('authenticated')
    })

    it('clears auth on 401 error', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiError('Unauthorized'), { status: 401 }),
        ),
      )
      useAuthStore.setState({ authStatus: 'authenticated' })

      await expect(useAuthStore.getState().fetchUser()).rejects.toThrow(
        'Session expired',
      )

      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
    })

    it('throws on non-401 errors without clearing auth', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiError('Network error')),
        ),
      )
      useAuthStore.setState({ authStatus: 'authenticated' })

      await expect(useAuthStore.getState().fetchUser()).rejects.toThrow(
        'Network error',
      )
      expect(useAuthStore.getState().authStatus).toBe('authenticated')
    })
  })

  describe('changePassword', () => {
    it('updates user on success', async () => {
      const updatedUser = { ...mockUser, must_change_password: false }
      server.use(
        http.post('/api/v1/auth/change-password', () =>
          HttpResponse.json(apiSuccess(updatedUser)),
        ),
      )
      useAuthStore.setState({
        authStatus: 'authenticated',
        user: { ...mockUser, must_change_password: true },
      })

      const result = await useAuthStore
        .getState()
        .changePassword('old', 'new')

      expect(result).toEqual(updatedUser)
      expect(useAuthStore.getState().user).toEqual(updatedUser)
    })
  })

  describe('logout', () => {
    it('calls logout API and sets unauthenticated', async () => {
      let logoutCalled = false
      server.use(
        http.post('/api/v1/auth/logout', () => {
          logoutCalled = true
          return HttpResponse.json(voidSuccess())
        }),
      )
      useAuthStore.setState({ authStatus: 'authenticated', user: mockUser })

      await useAuthStore.getState().logout()

      expect(logoutCalled).toBe(true)
      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('still clears auth when logout API fails', async () => {
      server.use(
        http.post('/api/v1/auth/logout', () =>
          HttpResponse.json(apiError('network error')),
        ),
      )
      useAuthStore.setState({ authStatus: 'authenticated', user: mockUser })

      await useAuthStore.getState().logout()

      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
      expect(useAuthStore.getState().user).toBeNull()
    })
  })

  describe('checkSession', () => {
    it('sets authenticated when server confirms session', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiSuccess(mockUser)),
        ),
      )

      await useAuthStore.getState().checkSession()

      expect(useAuthStore.getState().authStatus).toBe('authenticated')
      expect(useAuthStore.getState().user).toEqual(mockUser)
    })

    it('sets unauthenticated on 401 response', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiError('Unauthorized'), { status: 401 }),
        ),
      )

      await useAuthStore.getState().checkSession()

      expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('stays unknown on non-401 error (network/5xx)', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json(apiError('Network Error')),
        ),
      )

      await useAuthStore.getState().checkSession()

      expect(useAuthStore.getState().authStatus).toBe('unknown')
      expect(useAuthStore.getState().user).toBeNull()
    })
  })
})
