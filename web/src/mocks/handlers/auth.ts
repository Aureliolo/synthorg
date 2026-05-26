import { http, HttpResponse } from 'msw'
import type {
  changePassword,
  getMe,
  getWsTicket,
  listSessions,
  login,
  setup,
} from '@/api/endpoints/auth'
import type { AuthResponse, UserInfoResponse } from '@/api/types/auth'
import { successFor, voidSuccess } from './helpers'

const mockAuthResponse: AuthResponse = {
  expires_in: 86400,
  must_change_password: false,
}

function buildAuthUser(
  overrides: Partial<UserInfoResponse> = {},
): UserInfoResponse {
  return {
    id: 'user-default',
    username: 'default',
    role: 'ceo',
    must_change_password: false,
    org_roles: [],
    scoped_departments: [],
    ...overrides,
  }
}

// ── Storybook-facing named exports (keep for existing stories). ──

export const authLoginSuccess = [
  http.post('/api/v1/auth/login', () => {
    document.cookie = 'csrf_token=mock-csrf-token; path=/api'
    return HttpResponse.json(successFor<typeof login>(mockAuthResponse))
  }),
]

export const authSetupSuccess = [
  http.post('/api/v1/auth/setup', () => {
    document.cookie = 'csrf_token=mock-csrf-token; path=/api'
    return HttpResponse.json(successFor<typeof setup>(mockAuthResponse))
  }),
]

// ── Default test handlers: exhaustive coverage of every auth endpoint. ──

export const authHandlers = [
  http.post('/api/v1/auth/setup', () => {
    document.cookie = 'csrf_token=mock-csrf-token; path=/api'
    return HttpResponse.json(successFor<typeof setup>(mockAuthResponse))
  }),
  http.post('/api/v1/auth/login', () => {
    document.cookie = 'csrf_token=mock-csrf-token; path=/api'
    return HttpResponse.json(successFor<typeof login>(mockAuthResponse))
  }),
  http.post('/api/v1/auth/logout', () => HttpResponse.json(voidSuccess())),
  http.post('/api/v1/auth/change-password', () =>
    HttpResponse.json(successFor<typeof changePassword>(buildAuthUser())),
  ),
  http.get('/api/v1/auth/me', () =>
    HttpResponse.json(successFor<typeof getMe>(buildAuthUser())),
  ),
  http.post('/api/v1/auth/ws-ticket', () =>
    HttpResponse.json(
      successFor<typeof getWsTicket>({
        ticket: 'mock-ws-ticket',
        expires_in: 60,
      }),
    ),
  ),
  http.get('/api/v1/auth/sessions', () =>
    HttpResponse.json(successFor<typeof listSessions>([])),
  ),
  http.delete('/api/v1/auth/sessions/:id', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
