/**
 * Auth state management (Zustand).
 *
 * Manages cookie-based session lifecycle, login/logout flows, user profile,
 * and session validation. The JWT is stored in an HttpOnly cookie by the
 * backend -- the frontend never sees or manages the token directly.
 */

import { create } from 'zustand'
import * as authApi from '@/api/endpoints/auth'
import { setUnauthorizedHandler } from '@/api/unauthorized-handler'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage, isAxiosError } from '@/utils/errors'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'
import { createLogger } from '@/lib/logger'
import type { SessionInfo, UserInfoResponse } from '@/api/types/auth'
import type { HumanRole } from '@/api/types/enums'

const log = createLogger('auth')

// ── Store types ─────────────────────────────────────────────

/**
 * Tri-state auth status:
 * - 'unknown': initial state, session not yet validated (page load)
 * - 'authenticated': valid session confirmed by server
 * - 'unauthenticated': no session or session expired/invalid
 */
type AuthStatus = 'unknown' | 'authenticated' | 'unauthenticated'

interface AuthState {
  authStatus: AuthStatus
  user: UserInfoResponse | null
  loading: boolean

  // ── Active sessions ──
  sessions: SessionInfo[]
  sessionsLoading: boolean
  sessionsError: string | null

  login: (username: string, password: string) => Promise<void>
  setup: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  fetchUser: () => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<UserInfoResponse>
  fetchSessions: (scope?: 'own' | 'all') => Promise<void>
  revokeSession: (sessionId: string) => Promise<boolean>
  handleUnauthorized: () => void
  checkSession: () => Promise<void>
}

// ── Dev-only fake user ─────────────────────────────────────

const DEV_USER: UserInfoResponse | null = IS_DEV_AUTH_BYPASS
  ? { id: 'dev-user', username: 'developer', role: 'ceo', must_change_password: false, org_roles: ['owner'], scoped_departments: [] }
  : null

// ── One-shot redirect guard ─────────────────────────────────
//
// On reload with no session cookie, multiple inflight requests can
// land 401 in the same tick. Each invokes ``handleUnauthorized`` via
// the response interceptor, which without this guard would mean N
// concurrent ``window.location.href = '/login'`` assignments and N
// concurrent websocket disconnects, a visible login-redirect flicker.
// The guard ensures the redirect path runs
// at most once per page load; ``login()`` resets it on success so a
// later session expiry still works.
let unauthorizedRedirectInFlight = false

export function _resetUnauthorizedRedirectGuardForTests(): void {
  unauthorizedRedirectInFlight = false
}

// ── Store ───────────────────────────────────────────────────

async function performAuthFlow(
  set: (partial: Partial<AuthState>) => void,
  get: () => AuthState,
  authFn: () => Promise<{ expires_in: number }>,
): Promise<void> {
  set({ loading: true })
  try {
    await authFn()
    unauthorizedRedirectInFlight = false
    try {
      await get().fetchUser()
    } catch (fetchErr) {
      if (get().authStatus === 'unauthenticated') {
        throw new Error(
          'Your session expired before your account details could load. Please try again.',
          { cause: fetchErr },
        )
      }
      throw new Error(
        'Authentication succeeded but your account details could not be loaded. Check your connection and try again.',
        { cause: fetchErr },
      )
    }
    if (!get().user) {
      get().handleUnauthorized()
      throw new Error(
        'Authentication succeeded but your account details could not be loaded. Please try again.',
      )
    }
  } finally {
    set({ loading: false })
  }
}

async function fetchUserImpl(
  set: (partial: Partial<AuthState>) => void,
  get: () => AuthState,
): Promise<void> {
  if (
    get().authStatus === 'authenticated'
    && get().user
    && !IS_DEV_AUTH_BYPASS
  ) return
  try {
    const user = await authApi.getMe()
    set({ user, authStatus: 'authenticated' })
  } catch (err) {
    if (isAxiosError(err) && err.response?.status === 401) {
      log.warn('Session expired or invalid, clearing auth')
      get().handleUnauthorized()
      throw new Error('Session expired. Please log in again.', { cause: err })
    }
    log.error('Failed to fetch user profile:', getErrorMessage(err))
    throw err
  }
}

function handleUnauthorizedImpl(
  set: (partial: Partial<AuthState>) => void,
): void {
  if (unauthorizedRedirectInFlight) return
  unauthorizedRedirectInFlight = true
  set({ authStatus: 'unauthenticated', user: null })
  import('@/stores/websocket')
    .then(({ useWebSocketStore }) => {
      useWebSocketStore.getState().disconnect()
    })
    .catch(() => {
      // Best-effort -- import may fail during HMR or teardown.
    })
  const currentPath = window.location.pathname
  if (currentPath !== '/login' && currentPath !== '/setup') {
    window.location.href = '/login'
  }
}

async function checkSessionImpl(
  set: (partial: Partial<AuthState>) => void,
): Promise<void> {
  if (IS_DEV_AUTH_BYPASS) {
    set({ authStatus: 'authenticated', user: DEV_USER })
    return
  }
  try {
    const user = await authApi.getMe()
    set({ authStatus: 'authenticated', user })
  } catch (err) {
    if (isAxiosError(err) && err.response?.status === 401) {
      set({ authStatus: 'unauthenticated', user: null })
    } else {
      log.error('Session check failed:', getErrorMessage(err))
      set({ authStatus: 'unknown', user: null })
    }
  }
}

async function fetchSessionsImpl(
  set: (partial: Partial<AuthState>) => void,
  scope: 'own' | 'all',
): Promise<void> {
  set({ sessionsLoading: true, sessionsError: null })
  try {
    const sessions = await authApi.listSessions(scope)
    set({ sessions, sessionsLoading: false })
  } catch (err) {
    log.error('Failed to fetch sessions:', getErrorMessage(err))
    set({ sessionsError: getErrorMessage(err), sessionsLoading: false })
  }
}

async function revokeSessionImpl(
  set: (partial: Partial<AuthState>) => void,
  get: () => AuthState,
  sessionId: string,
): Promise<boolean> {
  // Capture only the row we optimistically remove so a failure rollback
  // cannot clobber a concurrent refresh of the session list.
  const before = get().sessions
  const removed = before.find((s) => s.session_id === sessionId) ?? null
  set({ sessions: before.filter((s) => s.session_id !== sessionId) })
  try {
    await authApi.revokeSession(sessionId)
    useToastStore.getState().add({ variant: 'success', title: 'Session revoked' })
    return true
  } catch (err) {
    const current = get().sessions
    const alreadyBack = current.some((s) => s.session_id === sessionId)
    if (!alreadyBack && removed) set({ sessions: [removed, ...current] })
    log.error('Revoke session failed:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to revoke session'),
      description: getErrorMessage(err),
    })
    return false
  }
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  authStatus: IS_DEV_AUTH_BYPASS ? 'authenticated' : 'unknown',
  user: DEV_USER,
  loading: false,

  sessions: [],
  sessionsLoading: false,
  sessionsError: null,

  login: (username, password) =>
    performAuthFlow(
      set,
      get,
      () => authApi.login({ username, password }),
    ),
  setup: (username, password) =>
    performAuthFlow(
      set,
      get,
      () => authApi.setup({ username, password }),
    ),
  async logout() {
    try {
      await authApi.logout()
    } catch (err) {
      log.warn('Logout API call failed:', getErrorMessage(err))
    }
    get().handleUnauthorized()
  },
  fetchUser: () => fetchUserImpl(set, get),
  fetchSessions: (scope = 'own') => fetchSessionsImpl(set, scope),
  revokeSession: (sessionId) => revokeSessionImpl(set, get, sessionId),
  async changePassword(currentPassword, newPassword) {
    set({ loading: true })
    try {
      const result = await authApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      set({ user: result })
      return result
    } catch (err) {
      throw new Error(getErrorMessage(err), { cause: err })
    } finally {
      set({ loading: false })
    }
  },
  handleUnauthorized: () => handleUnauthorizedImpl(set),
  checkSession: () => checkSessionImpl(set),
}))

// ── 401 handler registration ────────────────────────────────
//
// `api/client` calls `notifyUnauthorized()` from its 401 response
// interceptor; this side-effect wires the auth store as the listener.
// Done at module init (not lazy) so the handler is in place before
// any HTTP call lands a 401. Lives here, not in `main.tsx`, because
// the auth store owns its own lifecycle behaviour.
setUnauthorizedHandler(() => {
  useAuthStore.getState().handleUnauthorized()
})

// ── Selector hooks ──────────────────────────────────────────

export const useAuthStatus = () => useAuthStore((s) => s.authStatus)

export const useIsAuthenticated = () => useAuthStore((s) => s.authStatus === 'authenticated')

export const useUserRole = () => useAuthStore((s): HumanRole | null => s.user?.role ?? null)

export const useMustChangePassword = () =>
  useAuthStore((s) => s.user?.must_change_password ?? false)
