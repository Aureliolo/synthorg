import { http, HttpResponse } from 'msw'
import {
  _resetUnauthorizedRedirectGuardForTests,
  useAuthStore,
} from '@/stores/auth'
import { apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { UserInfoResponse } from '@/api/types/auth'

// Bypass ON for this file: checkSession should obtain a REAL session via the
// password-free /auth/dev-login endpoint, not a fake user.
vi.mock('@/utils/dev', () => ({ IS_DEV_AUTH_BYPASS: true }))

// handleUnauthorized dynamically imports the websocket store; stub it so the
// chain does not leak past the test body (active-handle gate).
const { wsDisconnectSpy, wsRetrySpy } = vi.hoisted(() => ({
  wsDisconnectSpy: vi.fn(),
  wsRetrySpy: vi.fn(),
}))
vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: {
    getState: () => ({ disconnect: wsDisconnectSpy, retry: wsRetrySpy }),
  },
}))

const ADMIN: UserInfoResponse = {
  id: 'admin',
  username: 'admin',
  role: 'ceo',
  must_change_password: false,
  org_roles: ['owner'],
  scoped_departments: [],
}

describe('auth store dev bypass auto-login', () => {
  beforeEach(() => {
    _resetUnauthorizedRedirectGuardForTests()
    useAuthStore.setState({ authStatus: 'unknown', user: null })
    wsDisconnectSpy.mockClear()
    wsRetrySpy.mockClear()
  })

  it('auto-logs-in as the admin via /auth/dev-login when no session exists', async () => {
    let meCalls = 0
    server.use(
      http.get('/api/v1/auth/me', () => {
        meCalls += 1
        // First probe: no session. After dev-login mints one, return the admin.
        if (meCalls === 1) return new HttpResponse(null, { status: 401 })
        return HttpResponse.json(apiSuccess(ADMIN))
      }),
      http.post('/api/v1/auth/dev-login', () =>
        HttpResponse.json(apiSuccess({ expires_in: 86400, must_change_password: false })),
      ),
    )

    await useAuthStore.getState().checkSession()

    expect(useAuthStore.getState().authStatus).toBe('authenticated')
    expect(useAuthStore.getState().user?.username).toBe('admin')
  })

  it('re-mints an expired session in place via handleUnauthorized', async () => {
    // A mid-session 401 (cookie expiry) must not bounce a dev to the
    // login screen: the session is re-minted password-free and the
    // websocket transport (which stops on an auth-failed ticket) is
    // retried explicitly.
    server.use(
      http.post('/api/v1/auth/dev-login', () =>
        HttpResponse.json(apiSuccess({ expires_in: 86400, must_change_password: false })),
      ),
      http.get('/api/v1/auth/me', () => HttpResponse.json(apiSuccess(ADMIN))),
    )
    useAuthStore.setState({ authStatus: 'authenticated', user: ADMIN })

    useAuthStore.getState().handleUnauthorized()

    await vi.waitFor(() => {
      expect(wsRetrySpy).toHaveBeenCalledTimes(1)
    })
    expect(useAuthStore.getState().authStatus).toBe('authenticated')
    expect(useAuthStore.getState().user?.username).toBe('admin')
    expect(wsDisconnectSpy).not.toHaveBeenCalled()
  })

  it('shows the login screen when /auth/dev-login is disabled (404)', async () => {
    server.use(
      http.get('/api/v1/auth/me', () => new HttpResponse(null, { status: 401 })),
      http.post('/api/v1/auth/dev-login', () => new HttpResponse(null, { status: 404 })),
    )

    await useAuthStore.getState().checkSession()

    expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
  })
})
