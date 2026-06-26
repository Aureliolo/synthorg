import { useEffect } from 'react'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'
import { useAuthStatus, useAuthStore } from '@/stores/auth'

/**
 * DEV ONLY root-level auth bootstrap.
 *
 * The setup wizard renders OUTSIDE AuthGuard (first-run must work before any
 * login exists), so the dev bypass's auto-login -- which AuthGuard triggers via
 * checkSession -- would never fire on /setup, leaving every wizard request
 * unauthenticated. This gate runs checkSession once on mount under the bypass
 * and blocks rendering until it resolves, so EVERY route (including the wizard)
 * starts with a real session (the password-free /auth/dev-login). It is a
 * no-op when the bypass is off, so the normal login / first-run flow is
 * untouched. If auto-login fails (no admin), it falls through to the normal
 * unauthenticated rendering, so account creation is never skipped.
 */
export function DevAuthBootstrap({ children }: { children: React.ReactNode }) {
  const authStatus = useAuthStatus()
  const checkSession = useAuthStore((s) => s.checkSession)

  useEffect(() => {
    if (IS_DEV_AUTH_BYPASS && authStatus === 'unknown') {
      void checkSession()
    }
  }, [authStatus, checkSession])

  if (IS_DEV_AUTH_BYPASS && authStatus === 'unknown') {
    return (
      <div
        className="flex h-screen items-center justify-center"
        role="status"
        aria-live="polite"
      >
        <span className="text-sm text-muted-foreground">Signing in...</span>
      </div>
    )
  }
  return <>{children}</>
}
