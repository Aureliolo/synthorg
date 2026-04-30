import { useCallback, useEffect, useState } from 'react'
import { createLogger } from '@/lib/logger'
import { InputField } from '@/components/ui/input-field'
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
    function withTimeout<T>(work: Promise<T>): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          reject(new Error('password-policy fetch timed out'))
        }, POLICY_TIMEOUT_MS)
        work.then(
          (value) => { window.clearTimeout(timer); resolve(value) },
          (err: unknown) => { window.clearTimeout(timer); reject(err instanceof Error ? err : new Error(String(err))) },
        )
      })
    }
    let lastErr: unknown = null
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const status = await withTimeout(getSetupStatus())
        setMinPasswordLength(status.min_password_length ?? DEFAULT_MIN_PASSWORD_LENGTH)
        setPolicyLoading(false)
        return
      } catch (err) {
        lastErr = err
        log.warn('password-policy fetch attempt failed', {
          attempt: attempt + 1,
          error: getErrorMessage(err),
        })
      }
    }
    log.error('Failed to fetch setup status after retries:', sanitizeForLog(lastErr))
    setPolicyError(getErrorMessage(lastErr))
    setPolicyLoading(false)
  }, [])

  useEffect(() => {
    void fetchPolicy()
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
        />

        {policyError && (
          <ErrorBanner
            variant="section"
            severity="error"
            title="Could not load password policy"
            description="The server policy may be stricter than the default. Retry before creating your account so validation matches the server."
            onRetry={() => void fetchPolicy()}
          />
        )}

        {error && (
          <ErrorBanner variant="section" severity="error" title="Could not create account" description={error} />
        )}

        <Button
          onClick={handleSubmit}
          disabled={loading || policyLoading || policyError !== null}
          className="w-full"
        >
          {loading ? 'Creating Account...' : 'Create Account'}
        </Button>
      </div>
    </div>
  )
}
