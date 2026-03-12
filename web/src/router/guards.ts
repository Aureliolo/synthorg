import type { NavigationGuardNext, RouteLocationNormalized } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

/**
 * Navigation guard that redirects unauthenticated users to /login.
 * Allows /login and /setup routes without authentication.
 */
export function authGuard(
  to: RouteLocationNormalized,
  _from: RouteLocationNormalized,
  next: NavigationGuardNext,
): void {
  const auth = useAuthStore()
  const publicRoutes = ['/login', '/setup']

  if (publicRoutes.includes(to.path)) {
    // If already authenticated, redirect away from login
    if (auth.isAuthenticated && to.path === '/login') {
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
