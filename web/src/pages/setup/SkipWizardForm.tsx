import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { InputField } from '@/components/ui/input-field'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useSetupStore } from '@/stores/setup'
import { useToastStore } from '@/stores/toast'

interface SkipWizardSubmit {
  companyName: string
  setCompanyName: (value: string) => void
  error: string | null
  setError: (value: string | null) => void
  loading: boolean
  handleSubmit: (e?: React.SyntheticEvent) => Promise<void>
}

function useSkipWizardSubmit(): SkipWizardSubmit {
  const navigate = useNavigate()
  const [companyName, setCompanyName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const submitCompany = useSetupWizardStore((s) => s.submitCompany)
  const setCompanyNameStore = useSetupWizardStore((s) => s.setCompanyName)
  const wizardCompleteSetup = useSetupWizardStore((s) => s.completeSetup)

  const handleSubmit = useCallback(async (e?: React.SyntheticEvent) => {
    e?.preventDefault()
    const trimmed = companyName.trim()
    if (!trimmed) {
      setError('Company name is required')
      return
    }
    setLoading(true)
    setError(null)
    setCompanyNameStore(trimmed)
    // Both store mutations own their error UX and do not throw
    // (``submitCompany`` sets ``companyError``; ``completeSetup`` sets
    // ``completionError``). The caller must not wrap them in
    // try/catch; the try/finally here exists ONLY to guarantee the
    // ``loading`` flag is cleared, never to swallow store errors.
    try {
      await submitCompany()
      const afterCompany = useSetupWizardStore.getState()
      if (afterCompany.companyResponse === null) {
        setError(
          afterCompany.companyError
            ?? 'Company creation failed. Please try again.',
        )
        return
      }
      await wizardCompleteSetup()
      const afterComplete = useSetupWizardStore.getState()
      if (afterComplete.completionError !== null) {
        // Partial success: the company exists (companyResponse is
        // non-null by construction here) but completion failed. Keep
        // the distinct partial-success message so the operator knows
        // not to recreate the company on retry.
        setError(
          `Company '${trimmed}' was created, but setup completion failed: ${afterComplete.completionError}. Open the wizard's Complete step or reload the page to retry.`,
        )
        return
      }
      if (afterComplete.completionWarning !== null) {
        // Completion succeeded with a non-fatal warning (e.g. embedder
        // auto-selection failed). Do NOT mark setup complete or
        // navigate: companyResponse is now non-null, so CompleteStep
        // re-renders its main UI (no longer SkipWizardForm) and
        // surfaces the warning with an explicit continue CTA.
        return
      }
      useSetupStore.setState({ setupComplete: true })
      useToastStore.getState().add({
        variant: 'success',
        title: `Welcome to ${trimmed}!`,
        description: 'Setup complete. Configure everything else in Settings.',
      })
      void navigate('/')
    } finally {
      setLoading(false)
    }
  }, [companyName, setCompanyNameStore, submitCompany, wizardCompleteSetup, navigate])

  return { companyName, setCompanyName, error, setError, loading, handleSubmit }
}

export function SkipWizardForm() {
  const { companyName, setCompanyName, error, setError, loading, handleSubmit } =
    useSkipWizardSubmit()

  return (
    <div className="mx-auto max-w-md space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Quick Setup</h2>
        <p className="text-sm text-muted-foreground">
          Skip the wizard and configure everything later in Settings.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-border bg-card p-card">
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
