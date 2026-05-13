import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { InputField } from '@/components/ui/input-field'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useSetupStore } from '@/stores/setup'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'

export function SkipWizardForm() {
  const navigate = useNavigate()
  const [companyName, setCompanyName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const submitCompany = useSetupWizardStore((s) => s.submitCompany)
  const setCompanyNameStore = useSetupWizardStore((s) => s.setCompanyName)
  const wizardCompleteSetup = useSetupWizardStore((s) => s.completeSetup)

  const handleSubmit = useCallback(async (e?: React.FormEvent) => {
    e?.preventDefault()
    const trimmed = companyName.trim()
    if (!trimmed) {
      setError('Company name is required')
      return
    }
    setLoading(true)
    setError(null)
    setCompanyNameStore(trimmed)
    try {
      // submitCompany handles its own errors in the store (sets
      // companyError instead of throwing). Read the durable
      // companyResponse from the store after the call to detect a
      // creation failure -- a try/catch around submitCompany alone
      // would never fire. Only wizardCompleteSetup can throw, so
      // the catch block below is reserved for that path.
      await submitCompany()
      const wizardState = useSetupWizardStore.getState()
      if (wizardState.companyResponse === null) {
        setError(
          wizardState.companyError
            ?? 'Company creation failed. Please try again.',
        )
        return
      }
      await wizardCompleteSetup()
      useSetupStore.setState({ setupComplete: true })
      useToastStore.getState().add({
        variant: 'success',
        title: `Welcome to ${trimmed}!`,
        description: 'Setup complete. Configure everything else in Settings.',
      })
      navigate('/')
    } catch (err) {
      // The catch path now only runs for wizardCompleteSetup throws
      // (submitCompany never throws -- see above). companyResponse
      // is by construction non-null here, so the error is always a
      // partial-success: company exists, completion failed.
      const baseMessage = getErrorMessage(err)
      setError(
        `Company '${trimmed}' was created, but setup completion failed: ${baseMessage}. Open the wizard's Complete step or reload the page to retry.`,
      )
    } finally {
      setLoading(false)
    }
  }, [companyName, setCompanyNameStore, submitCompany, wizardCompleteSetup, navigate])

  return (
    <div className="mx-auto max-w-md space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Quick Setup</h2>
        <p className="text-sm text-muted-foreground">
          Skip the wizard and configure everything later in Settings.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-border bg-card p-6">
        <InputField
          label="Company Name"
          required
          value={companyName}
          onChange={(e) => setCompanyName(e.currentTarget.value)}
          placeholder="Your organization name"
          disabled={loading}
        />

        {error && (
          <ErrorBanner
            variant="section"
            severity="error"
            title="Could not skip the wizard"
            description={error}
            onRetry={() => {
              setError(null)
              void handleSubmit()
            }}
          />
        )}

        <Button
          type="submit"
          disabled={loading || companyName.trim().length === 0}
          className="w-full"
        >
          {loading ? 'Setting up...' : 'Complete Setup'}
        </Button>
      </form>
    </div>
  )
}
