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
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'
import { createLogger } from '@/lib/logger'
import type { UserInfoResponse } from '@/api/types/auth'
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

  login: (username: string, password: string) => Promise<void>
  setup: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  fetchUser: () => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<UserInfoResponse>
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
// the response interceptor, which previously meant N concurrent
// ``window.location.href = '/login'`` assignments and N concurrent
// websocket disconnects -- a flicker that the audit's "auth-on-reload
// glitch" report calls out. The guard ensures the redirect path runs
// at most once per page load; ``login()`` resets it on success so a
// later session expiry still works.
let unauthorizedRedirectInFlight = false

export function _resetUnauthorizedRedirectGuardForTests(): void {
  unauthorizedRedirectInFlight = false
}

// ── Store ───────────────────────────────────────────────────

export const useAuthStore = create<AuthState>()((set, get) => {
  /** Common post-auth flow: authenticate, fetch user profile, handle failures. */
  async function performAuthFlow(
    authFn: () => Promise<{ expires_in: number }>,
    flowName: string,
  ): Promise<void> {
    set({ loading: true })
    try {
      await authFn()
      // A fresh authentication attempt clears the one-shot redirect
      // guard so a future session expiry can still drive a clean
      // redirect, not no-op against a stale "already redirecting" flag.
      unauthorizedRedirectInFlight = false
      // Cookie is set by the server. Fetch user profile to confirm session.
      try {
        await get().fetchUser()
      } catch (fetchErr) {
        // fetchUser already calls handleUnauthorized() on 401 before throwing
        if (get().authStatus === 'unauthenticated') {
          throw new Error(`${flowName} failed: session expired. Please try again.`, { cause: fetchErr })
        }
        // Don't invalidate the session on transient errors (network, 5xx).
        // The auth succeeded; the profile load can be retried.
        throw new Error(`${flowName} succeeded but failed to load user profile. Please check your connection and try again.`, { cause: fetchErr })
      }
      if (!get().user) {
        get().handleUnauthorized()
        throw new Error(`${flowName} succeeded but failed to load user profile. Please try again.`)
      }
    } finally {
      set({ loading: false })
    }
  }

  return {
    authStatus: IS_DEV_AUTH_BYPASS ? 'authenticated' : 'unknown',
    user: DEV_USER,
    loading: false,

    async login(username: string, password: string) {
      await performAuthFlow(() => authApi.login({ username, password }), 'Login')
    },

    async setup(username: string, password: string) {
      await performAuthFlow(() => authApi.setup({ username, password }), 'Setup')
    },

    async logout() {
      try {
        await authApi.logout()
      } catch (err) {
        // Log but don't block -- server may have already cleared the cookie
        log.warn('Logout API call failed:', getErrorMessage(err))
      }
      get().handleUnauthorized()
    },

    async fetchUser() {
      if (get().authStatus === 'authenticated' && get().user && !IS_DEV_AUTH_BYPASS) return
      try {
        const user = await authApi.getMe()
        set({ user, authStatus: 'authenticated' })
      } catch (err) {
        // Only clear auth on 401 (invalid/expired session)
        if (isAxiosError(err) && err.response?.status === 401) {
          log.warn('Session expired or invalid, clearing auth')
          get().handleUnauthorized()
          throw new Error('Session expired. Please log in again.', { cause: err })
        } else {
          log.error('Failed to fetch user profile:', getErrorMessage(err))
          throw err
        }
      }
    },

    async changePassword(currentPassword: string, newPassword: string) {
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

    handleUnauthorized() {
      // One-shot guard against concurrent 401s during a single page
      // load. Without this, a burst of inflight requests each schedule
      // their own redirect + websocket disconnect, producing the
      // flicker the audit calls out. The guard resets on a successful
      // login so a later session expiry still triggers cleanly.
      if (unauthorizedRedirectInFlight) return
      unauthorizedRedirectInFlight = true
      set({ authStatus: 'unauthenticated', user: null })
      // Tear down WebSocket transport so it stops reconnecting.
      import('@/stores/websocket').then(({ useWebSocketStore }) => {
        useWebSocketStore.getState().disconnect()
      }).catch(() => {
        // Best-effort -- import may fail during HMR or teardown.
      })
      // Hard redirect to login -- intentionally uses window.location (not
      // react-router) because this runs in a Zustand store outside the
      // React tree.
      const currentPath = window.location.pathname
      if (currentPath !== '/login' && currentPath !== '/setup') {
        window.location.href = '/login'
      }
    },

    async checkSession() {
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
          // Non-auth error (network, 5xx) -- don't drop valid sessions.
          log.error('Session check failed:', getErrorMessage(err))
          set({ authStatus: 'unknown', user: null })
        }
      }
    },
  }
})

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
