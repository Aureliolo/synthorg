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
vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: { getState: () => ({ disconnect: vi.fn() }) },
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

  it('shows the login screen when /auth/dev-login is disabled (404)', async () => {
    server.use(
      http.get('/api/v1/auth/me', () => new HttpResponse(null, { status: 401 })),
      http.post('/api/v1/auth/dev-login', () => new HttpResponse(null, { status: 404 })),
    )

    await useAuthStore.getState().checkSession()

    expect(useAuthStore.getState().authStatus).toBe('unauthenticated')
  })
})
