import { useEffect } from 'react'
import { Navigate, Outlet } from 'react-router'
import { useAuthStore, useAuthStatus, useIsAuthenticated } from '@/stores/auth'
import { useSetupStore } from '@/stores/setup'
import { ROUTES } from './routes'

/** Shared full-screen loading indicator for guard states. */
function FullScreenLoading() {
  return (
    <div
      className="flex h-screen items-center justify-center"
      role="status"
      aria-live="polite"
    >
      <div className="text-text-secondary text-sm">Loading...</div>
    </div>
  )
}

/** Shared full-screen error indicator with retry. */
function FullScreenError({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className="flex h-screen flex-col items-center justify-center gap-4"
      role="alert"
    >
      <div className="text-text-secondary text-sm">
        Failed to check setup status.
      </div>
      <button
        type="button"
        onClick={onRetry}
        className={
          'rounded-md bg-primary px-4 py-2 text-sm'
          + ' font-medium text-primary-foreground transition-colors'
          + ' hover:bg-primary/80'
        }
      >
        Retry
      </button>
    </div>
  )
}

/**
 * Requires authentication. Redirects to /login if no session cookie.
 * Validates the session by calling checkSession() on mount when
 * authStatus is 'unknown' (page load / refresh).
 * Fail-closed: shows loading until validation completes.
 */
export function AuthGuard() {
  const authStatus = useAuthStatus()
  const checkSession = useAuthStore((s) => s.checkSession)

  useEffect(() => {
    if (authStatus === 'unknown') {
      checkSession()
    }
  }, [authStatus, checkSession])

  if (authStatus === 'unknown') {
    return <FullScreenLoading />
  }

  if (authStatus === 'unauthenticated') {
    return <Navigate to={ROUTES.LOGIN} replace />
  }

  return <Outlet />
}

/**
 * Requires setup to be complete. Redirects to /setup if not.
 * Shows a loading indicator while fetching setup status.
 * Shows an error with retry if the fetch fails.
 * Must be nested inside AuthGuard (setup check only applies to authenticated users).
 */
export function SetupGuard() {
  const setupComplete = useSetupStore((s) => s.setupComplete)
  const loading = useSetupStore((s) => s.loading)
  const error = useSetupStore((s) => s.error)
  const fetchSetupStatus = useSetupStore((s) => s.fetchSetupStatus)

  useEffect(() => {
    if (setupComplete === null && !loading && !error) {
      fetchSetupStatus()
    }
  }, [setupComplete, loading, error, fetchSetupStatus])

  if (error) {
    return <FullScreenError onRetry={fetchSetupStatus} />
  }

  if (setupComplete === null || loading) {
    return <FullScreenLoading />
  }

  if (!setupComplete) {
    return <Navigate to={ROUTES.SETUP} replace />
  }

  return <Outlet />
}

/**
 * Guest-only guard for /login. Redirects to dashboard if already authenticated.
 * Shows loading while auth status is being determined.
 */
export function GuestGuard({ children }: { children: React.ReactNode }) {
  const authStatus = useAuthStatus()
  const checkSession = useAuthStore((s) => s.checkSession)

  useEffect(() => {
    if (authStatus === 'unknown') {
      checkSession()
    }
  }, [authStatus, checkSession])

  if (authStatus === 'unknown') {
    return <FullScreenLoading />
  }

  if (authStatus === 'authenticated') {
    return <Navigate to={ROUTES.DASHBOARD} replace />
  }

  return <>{children}</>
}

/**
 * Prevents access to /setup when setup is already complete.
 * Allows unauthenticated access (setup page may be shown before first login).
 * Fail-closed: if the status fetch fails for an authenticated user,
 * redirects to dashboard rather than allowing setup access.
 */
export function SetupCompleteGuard({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useIsAuthenticated()
  const setupComplete = useSetupStore((s) => s.setupComplete)
  const loading = useSetupStore((s) => s.loading)
  const error = useSetupStore((s) => s.error)
  const fetchSetupStatus = useSetupStore((s) => s.fetchSetupStatus)

  useEffect(() => {
    if (isAuthenticated && setupComplete === null && !loading && !error) {
      fetchSetupStatus()
    }
  }, [isAuthenticated, setupComplete, loading, error, fetchSetupStatus])

  // If not authenticated, allow through (setup page handles its own auth flow)
  if (!isAuthenticated) {
    return <>{children}</>
  }

  // Fail-closed: if fetch failed for an authenticated user, redirect to
  // dashboard rather than allowing access to setup
  if (error) {
    return <Navigate to={ROUTES.DASHBOARD} replace />
  }

  // Authenticated -- check setup status
  if (setupComplete === null || loading) {
    return <FullScreenLoading />
  }

  if (setupComplete) {
    return <Navigate to={ROUTES.DASHBOARD} replace />
  }

  return <>{children}</>
}
