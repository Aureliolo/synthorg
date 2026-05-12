import { useCallback, useEffect, useRef, useState } from 'react'
import { createLogger } from '@/lib/logger'
import { InputField, PasswordVisibilityGroup } from '@/components/ui/input-field'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useAuthStore } from '@/stores/auth'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { getPasswordStrength } from '@/utils/password-strength'
import { getSetupStatus } from '@/api/endpoints/setup'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { cn } from '@/lib/utils'

const log = createLogger('setup')

const DEFAULT_MIN_PASSWORD_LENGTH = 12

export function AccountStep() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [minPasswordLength, setMinPasswordLength] = useState(DEFAULT_MIN_PASSWORD_LENGTH)
  const [policyError, setPolicyError] = useState<string | null>(null)
  const [policyLoading, setPolicyLoading] = useState(true)

  const authSetup = useAuthStore((s) => s.setup)
  const setAccountCreated = useSetupWizardStore((s) => s.setAccountCreated)
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)

  // Cancellation flag for ``fetchPolicy``: the effect below sets this
  // ref to ``true`` on unmount so the timed-out / mid-retry response
  // handler can no-op instead of writing setState into a torn-down
  // component (matches the pattern used by CoordinationMetricsPage /
  // MetaAnalyticsPage etc.). Stored in a ref so the
  // ``useCallback``-memoised ``fetchPolicy`` reads the same flag the
  // effect's cleanup mutates without being re-created on every render.
  const cancelledRef = useRef(false)
  // Active timer IDs (policy-fetch timeout + retry backoff). Tracked so
  // that an unmount can clear them eagerly instead of letting them
  // tick to completion as zombies; the cancelledRef guard alone would
  // prevent stray setState calls but leaves the underlying timeouts
  // pinned to the event loop until they fire.
  const pendingTimersRef = useRef<Set<number>>(new Set())

  // Read backend-configured min password length. Surfaced as an error so
  // users cannot submit under the default policy if the server has a stricter
  // rule (otherwise the create-account POST would fail with a confusing error).
  // The fetch is wrapped in a 5-second timeout and retries once on transient
  // failure; otherwise a slow / hung server would block the entire setup
  // wizard with the form disabled and no recovery path.
  const fetchPolicy = useCallback(async () => {
    setPolicyLoading(true)
    setPolicyError(null)
    const POLICY_TIMEOUT_MS = 5_000
    const timers = pendingTimersRef.current
    function withTimeout<T>(work: Promise<T>): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          timers.delete(timer)
          reject(new Error('password-policy fetch timed out'))
        }, POLICY_TIMEOUT_MS)
        timers.add(timer)
        work.then(
          (value) => { timers.delete(timer); window.clearTimeout(timer); resolve(value) },
          (err: unknown) => { timers.delete(timer); window.clearTimeout(timer); reject(err instanceof Error ? err : new Error(String(err))) },
        )
      })
    }
    let lastErr: unknown = null
    const attemptErrors: string[] = []
    const MAX_ATTEMPTS = 2
    const BACKOFF_MS = 500
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
      try {
        const status = await withTimeout(getSetupStatus())
        if (cancelledRef.current) return
        setMinPasswordLength(status.min_password_length ?? DEFAULT_MIN_PASSWORD_LENGTH)
        setPolicyLoading(false)
        return
      } catch (err) {
        if (cancelledRef.current) return
        lastErr = err
        attemptErrors.push(getErrorMessage(err))
        if (attempt + 1 < MAX_ATTEMPTS) {
          // Small backoff so a transient back-pressure response is not
          // hammered into a second failure inside the same event tick.
          await new Promise<void>((resolve) => {
            const timer = window.setTimeout(() => {
              timers.delete(timer)
              resolve()
            }, BACKOFF_MS)
            timers.add(timer)
          })
          if (cancelledRef.current) return
        }
      }
    }
    if (cancelledRef.current) return
    // SEC-1: dynamic strings (``attemptErrors`` entries, ``lastErr``
    // message) go through sanitizeForLog before reaching the log
    // pipeline.
    log.error('Failed to fetch setup status after retries', {
      attempts: attemptErrors.map((entry) => sanitizeForLog(entry)),
      error: sanitizeForLog(getErrorMessage(lastErr)),
    })
    setPolicyError(getErrorMessage(lastErr))
    setPolicyLoading(false)
  }, [])

  useEffect(() => {
    cancelledRef.current = false
    const timers = pendingTimersRef.current
    void fetchPolicy()
    return () => {
      cancelledRef.current = true
      for (const timer of timers) window.clearTimeout(timer)
      timers.clear()
    }
  }, [fetchPolicy])

  const strength = getPasswordStrength(password)

  const handleSubmit = useCallback(async () => {
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

    setLoading(true)
    try {
      await authSetup(username.trim(), password)
      setAccountCreated(true)
      markStepComplete('account')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create account')
    } finally {
      setLoading(false)
    }
  }, [username, password, confirmPassword, minPasswordLength, authSetup, setAccountCreated, markStepComplete])

  return (
    <div className="mx-auto max-w-md space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Create Admin Account</h2>
        <p className="text-sm text-muted-foreground">
          Set up your administrator account to get started.
        </p>
      </div>

      <div className="space-y-4 rounded-lg border border-border bg-card p-card">
        <InputField
          label="Username"
          required
          value={username}
          onChange={(e) => setUsername(e.currentTarget.value)}
          placeholder="admin"
          disabled={loading}
        />

        <PasswordVisibilityGroup>
          <div className="space-y-1.5">
            <InputField
              label="Password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              placeholder={`Min ${minPasswordLength} characters`}
              disabled={loading}
              hint={`Min ${minPasswordLength} characters`}
              autoComplete="new-password"
            />
            {password.length > 0 && (
              <div className="flex items-center gap-2">
                <div className="h-1.5 flex-1 rounded-full bg-border">
                  <div
                    className={cn('h-full rounded-full transition-all', strength.color)}
                    style={{ width: `${strength.percent}%` }}
                  />
                </div>
                <span className="text-compact text-muted-foreground">{strength.label}</span>
              </div>
            )}
          </div>

          <InputField
            label="Confirm Password"
            type="password"
            required
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.currentTarget.value)}
            placeholder="Repeat password"
            disabled={loading}
            error={confirmPassword.length > 0 && password !== confirmPassword ? 'Passwords do not match' : null}
            autoComplete="new-password"
          />
        </PasswordVisibilityGroup>

        {policyError && (
          <ErrorBanner
            variant="section"
            severity="warning"
            title="Could not load password policy"
            description={
              `Falling back to a ${minPasswordLength}-character minimum. `
              + `If the server requires more, the create-account request will be rejected with a clear error. `
              + `You can retry the policy fetch or proceed; either path keeps the wizard moving forwards.`
            }
            onRetry={() => void fetchPolicy()}
          />
        )}

        {error && (
          <ErrorBanner variant="section" severity="error" title="Could not create account" description={error} />
        )}

        <Button
          onClick={handleSubmit}
          disabled={loading || policyLoading}
          className="w-full"
        >
          {loading ? 'Creating Account...' : 'Create Account'}
        </Button>
      </div>
    </div>
  )
}
