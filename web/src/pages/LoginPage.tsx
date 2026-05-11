import { useCallback, useEffect, useState } from 'react'
import { InputField, PasswordVisibilityGroup } from '@/components/ui/input-field'
import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/auth'
import { useLoginLockout } from '@/hooks/useLoginLockout'
import { getSetupStatus } from '@/api/endpoints/setup'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { MIN_PASSWORD_LENGTH } from '@/utils/constants'

const log = createLogger('LoginPage')

type Mode = 'loading' | 'login' | 'setup'

export default function LoginPage() {
  const [mode, setMode] = useState<Mode>('loading')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [minPasswordLength, setMinPasswordLength] = useState(MIN_PASSWORD_LENGTH)

  const login = useAuthStore((s) => s.login)
  const setup = useAuthStore((s) => s.setup)
  const { locked, checkAndClearLockout, recordFailure, reset } = useLoginLockout()

  // Detect first-run vs normal login on mount.
  // Fail-open on error: assume setup is complete so the login form
  // still renders, then SetupGuard re-checks after authentication.
  // Log structured context (status code, error message) so operators
  // can diagnose pre-auth fetch failures instead of silently falling
  // back to the login form. Without the log, a misconfigured API
  // base URL or a 5xx during boot has no signal.
  useEffect(() => {
    let cancelled = false
    getSetupStatus()
      .then((status) => {
        if (cancelled) return
        if (status.needs_admin) {
          setMode('setup')
          setMinPasswordLength(status.min_password_length ?? MIN_PASSWORD_LENGTH)
        } else {
          setMode('login')
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // Wrap the dynamic error string with sanitizeForLog before
        // embedding in the structured log payload (SEC-1: never let
        // attacker-controlled bytes reach the log pipeline raw).
        log.error('LoginPage setup-status check failed', {
          error: sanitizeForLog(getErrorMessage(err)),
          statusCode: isAxiosError(err) ? err.response?.status ?? null : null,
        })
        setMode('login')
      })
    return () => { cancelled = true }
  }, [])

  const handleLogin = useCallback(async () => {
    setError(null)
    if (!username.trim()) {
      setError('Username is required')
      return
    }
    if (!password) {
      setError('Password is required')
      return
    }
    if (checkAndClearLockout()) return

    const trimmedUsername = username.trim()
    setSubmitting(true)
    try {
      await login(trimmedUsername, password)
      reset()
    } catch (err) {
      const lockoutMsg = recordFailure(err)
      setError(lockoutMsg ?? getErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [username, password, login, checkAndClearLockout, recordFailure, reset])

  const handleSetup = useCallback(async () => {
    setError(null)
    if (!username.trim()) {
      setError('Username is required')
      return
    }
    if (password.length < minPasswordLength) {
      setError(`Password must be at least ${minPasswordLength} characters`)
      return
    }
    // eslint-disable-next-line security/detect-possible-timing-attacks -- client-side UI validation of user's own input
    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }

    const trimmedUsername = username.trim()
    setSubmitting(true)
    try {
      await setup(trimmedUsername, password)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [username, password, confirmPassword, minPasswordLength, setup])

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()
      if (mode === 'loading') return
      if (mode === 'setup') {
        void handleSetup()
      } else {
        void handleLogin()
      }
    },
    [mode, handleSetup, handleLogin],
  )

  const isLoginMode = mode === 'login'
  const disabled = submitting || (locked && isLoginMode)

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-border bg-card p-8 space-y-6"
        >
          {/* Wordmark */}
          <p className="text-center font-sans text-2xl font-bold text-accent">
            SynthOrg
          </p>

          {mode === 'loading' ? (
            <p className="text-center text-sm text-muted-foreground">
              Checking setup status...
            </p>
          ) : (
            <>
              {/* Heading */}
              <div className="space-y-1">
                <h1 className="text-lg font-semibold text-foreground">
                  {mode === 'setup' ? 'Create Admin Account' : 'Sign In'}
                </h1>
                {mode === 'setup' && (
                  <p className="text-sm text-muted-foreground">
                    Set up your administrator account to get started.
                  </p>
                )}
              </div>

              {/* Fields */}
              <div className="space-y-4">
                <InputField
                  label="Username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.currentTarget.value)}
                  disabled={disabled}
                  autoComplete="username"
                  autoFocus
                />

                <PasswordVisibilityGroup>
                  <InputField
                    label="Password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.currentTarget.value)}
                    disabled={disabled}
                    autoComplete={mode === 'setup' ? 'new-password' : 'current-password'}
                    hint={mode === 'setup' ? `At least ${minPasswordLength} characters` : undefined}
                  />

                  {mode === 'setup' && (
                    <InputField
                      label="Confirm Password"
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.currentTarget.value)}
                      disabled={disabled}
                      autoComplete="new-password"
                    />
                  )}
                </PasswordVisibilityGroup>
              </div>

              {/* Error */}
              {error && (
                <div
                  role="alert"
                  className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger"
                >
                  {error}
                </div>
              )}

              {/* Lockout warning (login mode only) */}
              {locked && isLoginMode && !error && (
                <div
                  role="alert"
                  className="rounded-md border border-warning/30 bg-warning/5 p-card text-sm text-warning"
                >
                  Too many failed attempts. Please wait before trying again.
                </div>
              )}

              {/* Submit */}
              <Button type="submit" className="w-full" disabled={disabled}>
                {submitting
                  ? (mode === 'setup' ? 'Creating Account...' : 'Signing In...')
                  : (mode === 'setup' ? 'Create Account' : 'Sign In')}
              </Button>
            </>
          )}
        </form>
      </div>
    </div>
  )
}
