import type { NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

/**
 * Navigation guard that redirects unauthenticated users to /login.
 * Uses route.meta.requiresAuth to determine access control:
 * - Routes with requiresAuth: false are public (login, setup)
 * - All other routes require authentication
 * Redirects authenticated users away from public auth pages.
 * Preserves intended destination via `redirect` query param.
 */
export function authGuard(
  to: RouteLocationNormalized,
  _from: RouteLocationNormalized,
  next: NavigationGuardNext,
): void {
  const auth = useAuthStore()

  if (to.meta.requiresAuth === false) {
    // If already authenticated, redirect away from login/setup
    if (auth.isAuthenticated) {
      next('/')
      return
    }
    next()
    return
  }

  if (!auth.isAuthenticated) {
    const redirect = to.fullPath !== '/' ? to.fullPath : undefined
    next({ path: '/login', query: redirect ? { redirect } : undefined })
    return
  }

  // Enforce mustChangePassword — always normalize to settings?tab=user (password form)
  if (auth.mustChangePassword && !(to.name === 'settings' && to.query.tab === 'user')) {
    next({ name: 'settings', query: { tab: 'user' } })
    return
  }

  next()
}
