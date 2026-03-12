import type { NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

/**
 * Navigation guard that redirects unauthenticated users to /login.
 * Allows /login and /setup routes without authentication.
 * Redirects authenticated users away from /login and /setup.
 */
export function authGuard(
  to: RouteLocationNormalized,
  _from: RouteLocationNormalized,
  next: NavigationGuardNext,
): void {
  const auth = useAuthStore()
  const publicRoutes = ['/login', '/setup']

  if (publicRoutes.includes(to.path)) {
    // If already authenticated, redirect away from login and setup
    if (auth.isAuthenticated && (to.path === '/login' || to.path === '/setup')) {
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
