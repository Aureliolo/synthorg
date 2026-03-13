import type { NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

/**
 * Navigation guard that redirects unauthenticated users to /login.
 * Uses route.meta.requiresAuth to determine access control:
 * - Routes with requiresAuth: false are public (login, setup)
 * - All other routes require authentication
 * Redirects authenticated users away from public auth pages.
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
    next('/login')
    return
  }

  next()
}
