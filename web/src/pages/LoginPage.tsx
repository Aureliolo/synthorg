import { useCallback, useEffect, useState } from 'react'
import { useLocation } from 'react-router'
import { InputField, PasswordVisibilityGroup } from '@/components/ui/input-field'
import { wasInterrupted } from '@/router/return-to'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useAuthStore } from '@/stores/auth'
import { useLoginLockout } from '@/hooks/useLoginLockout'
import { getSetupStatus } from '@/api/endpoints/setup'
import { getErrorMessage, isAxiosError } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { MIN_PASSWORD_LENGTH } from '@/utils/constants'

const log = createLogger('LoginPage')

type Mode = 'loading' | 'login' | 'setup'

function validateLoginInputs(username: string, password: string): string | null {
  if (!username.trim()) return 'Username is required'
  if (!password) return 'Password is required'
  return null
}

function validateSetupInputs(
  username: string,
  password: string,
  confirmPassword: string,
  minPasswordLength: number,
): string | null {
  if (!username.trim()) return 'Username is required'
  if (password.length < minPasswordLength) {
    return `Password must be at least ${minPasswordLength} characters`
  }
  if (password !== confirmPassword) return 'Passwords do not match'
  return null
}

function loginButtonLabel(submitting: boolean, mode: Mode): string {
  if (submitting) return mode === 'setup' ? 'Creating Account...' : 'Signing In...'
  return mode === 'setup' ? 'Create Account' : 'Sign In'
}

interface LoginMode {
  mode: Mode
  minPasswordLength: number
}

function useLoginMode(): LoginMode {
  const [mode, setMode] = useState<Mode>('loading')
  const [minPasswordLength, setMinPasswordLength] = useState(MIN_PASSWORD_LENGTH)

  // Detect first-run vs normal login on mount. Fail-open on error:
  // assume setup is complete so the login form still renders, then
  // SetupGuard re-checks after authentication. Log structured context
  // (status code, error message) so operators can diagnose pre-auth
  // fetch failures instead of silently falling back to the login form.
  useEffect(() => {
    let cancelled = false
    getSetupStatus()
      .then((status) => {
        if (cancelled) return
        if (status.needs_admin) {
          setMode('setup')
          setMinPasswordLength(status.min_password_length)
        } else {
          setMode('login')
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // SEC-1: wrap the dynamic error string with sanitizeForLog
        // before embedding it in the structured log payload.
        log.error('LoginPage setup-status check failed', {
          error: sanitizeForLog(getErrorMessage(err)),
          statusCode: isAxiosError(err) ? err.response?.status ?? null : null,
        })
        setMode('login')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return { mode, minPasswordLength }
}

interface AuthForm {
  username: string
  setUsername: (value: string) => void
  password: string
  setPassword: (value: string) => void
  confirmPassword: string
  setConfirmPassword: (value: string) => void
  error: string | null
  submitting: boolean
  locked: boolean
  isLoginMode: boolean
  disabled: boolean
  handleSubmit: (e: React.SyntheticEvent) => void
}

function useAuthForm({ mode, minPasswordLength }: LoginMode): AuthForm {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const login = useAuthStore((s) => s.login)
  const setup = useAuthStore((s) => s.setup)
  const { locked, checkAndClearLockout, recordFailure, reset } = useLoginLockout()

  const handleLogin = useCallback(async () => {
    const validationError = validateLoginInputs(username, password)
    if (validationError) {
      setError(validationError)
      return
    }
    setError(null)
    if (checkAndClearLockout()) return
    setSubmitting(true)
    try {
      await login(username.trim(), password)
      reset()
    } catch (err) {
      const lockoutMsg = recordFailure(err)
      setError(lockoutMsg ?? getErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [username, password, login, checkAndClearLockout, recordFailure, reset])

  const handleSetup = useCallback(async () => {
    const validationError = validateSetupInputs(username, password, confirmPassword, minPasswordLength)
    if (validationError) {
      setError(validationError)
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await setup(username.trim(), password)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [username, password, confirmPassword, minPasswordLength, setup])

  const handleSubmit = useCallback(
    (e: React.SyntheticEvent) => {
      e.preventDefault()
      if (mode === 'loading') return
      if (mode === 'setup') void handleSetup()
      else void handleLogin()
    },
    [mode, handleSetup, handleLogin],
  )

  const isLoginMode = mode === 'login'
  const disabled = submitting || (locked && isLoginMode)

  return {
    username, setUsername, password, setPassword, confirmPassword, setConfirmPassword,
    error, submitting, locked, isLoginMode, disabled, handleSubmit,
  }
}

function LoginHeading({ mode }: { mode: Mode }) {
  return (
    <div className="space-y-1">
      <h1 className="text-lg font-semibold text-foreground">
        {mode === 'setup' ? 'Create Admin Account' : 'Sign In'}
      </h1>
      {mode === 'setup' && (
        <p className="text-sm text-text-secondary">
          Set up your administrator account to get started.
        </p>
      )}
    </div>
  )
}

/**
 * Say that this screen appeared because a session ended, not because the
 * operator navigated here.
 *
 * A session expiring mid-run replaced the page an operator was watching with
 * what looks like a fresh visit, and one did about 50 minutes into a live run
 * with a decomposition still in flight. Nothing said why, and the run itself
 * was unaffected, so the only thing the silence cost was the operator's
 * confidence that it had been.
 */
function SessionEndedNotice() {
  return (
    <ErrorBanner
      variant="inline"
      severity="info"
      title="Your session ended, so you were signed out"
      description="Signing in returns you to the page you were on; the org kept working in the meantime."
    />
  )
}

function LoginAlerts({
  error,
  locked,
  isLoginMode,
}: {
  error: string | null
  locked: boolean
  isLoginMode: boolean
}) {
  return (
    <>
      {error && (
        <div
          role="alert"
          className="rounded-md border border-danger/30 bg-danger/5 p-card text-sm text-danger"
        >
          {error}
        </div>
      )}
      {locked && isLoginMode && !error && (
        <div
          role="alert"
          className="rounded-md border border-warning/30 bg-warning/5 p-card text-sm text-warning"
        >
          Too many failed attempts. Please wait before trying again.
        </div>
      )}
    </>
  )
}

function LoginFields({
  mode,
  minPasswordLength,
  form,
}: {
  mode: Mode
  minPasswordLength: number
  form: AuthForm
}) {
  return (
    <>
      <LoginHeading mode={mode} />

      <div className="space-y-4">
        <InputField
          label="Username"
          type="text"
          value={form.username}
          onChange={(e) => form.setUsername(e.currentTarget.value)}
          disabled={form.disabled}
          autoComplete="username"
          autoFocus
        />

        <PasswordVisibilityGroup>
          <InputField
            label="Password"
            type="password"
            value={form.password}
            onChange={(e) => form.setPassword(e.currentTarget.value)}
            disabled={form.disabled}
            autoComplete={mode === 'setup' ? 'new-password' : 'current-password'}
            hint={mode === 'setup' ? `At least ${minPasswordLength} characters` : undefined}
          />

          {mode === 'setup' && (
            <InputField
              label="Confirm Password"
              type="password"
              value={form.confirmPassword}
              onChange={(e) => form.setConfirmPassword(e.currentTarget.value)}
              disabled={form.disabled}
              autoComplete="new-password"
            />
          )}
        </PasswordVisibilityGroup>
      </div>

      <LoginAlerts error={form.error} locked={form.locked} isLoginMode={form.isLoginMode} />

      <Button type="submit" className="w-full" disabled={form.disabled}>
        {loginButtonLabel(form.submitting, mode)}
      </Button>
    </>
  )
}

export default function LoginPage() {
  const { mode, minPasswordLength } = useLoginMode()
  const form = useAuthForm({ mode, minPasswordLength })
  const location = useLocation()
  // Only when signing in, never during first-run setup: nothing has expired
  // for somebody who has not had a session yet.
  const interrupted = mode === 'login' && wasInterrupted(location.search)

  return (
    // ``h-full`` (100% of the ``height:100%`` #root chain), NOT ``min-h-screen``
    // (100vh), for the reason AppLayout carries the same rule: 100vh ignores the
    // scrollbar/chrome gutter and overruns the real viewport. Here that was not
    // a cosmetic sliver. ``#root`` is ``overflow: hidden``, so centring inside a
    // box taller than the viewport put the Sign In button below the fold with
    // nothing to scroll, and the product could not be signed into at all until
    // the window was resized.
    <div className="flex h-full items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <form
          onSubmit={form.handleSubmit}
          className="rounded-lg border border-border bg-card p-8 space-y-section-gap"
        >
          {/* Wordmark */}
          <p className="text-center font-sans text-2xl font-bold text-accent">SynthOrg</p>

          {interrupted && <SessionEndedNotice />}

          {mode === 'loading' ? (
            <p className="text-center text-sm text-text-secondary">Checking setup status...</p>
          ) : (
            <LoginFields mode={mode} minPasswordLength={minPasswordLength} form={form} />
          )}
        </form>
      </div>
    </div>
  )
}
