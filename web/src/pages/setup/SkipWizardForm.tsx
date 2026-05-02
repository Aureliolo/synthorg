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
      await submitCompany()
      await wizardCompleteSetup()
      useSetupStore.setState({ setupComplete: true })
      useToastStore.getState().add({
        variant: 'success',
        title: `Welcome to ${trimmed}!`,
        description: 'Setup complete. Configure everything else in Settings.',
      })
      navigate('/')
    } catch (err) {
      // Discriminate via the store snapshot, not a local flag set
      // between awaits: a local flag race-conditions with any throw
      // that happens after submitCompany resolves but before the
      // assignment line executes. The store's companyResponse is
      // the durable source of truth for "did the company actually
      // get created?".
      const companyCreated =
        useSetupWizardStore.getState().companyResponse !== null
      const baseMessage = getErrorMessage(err)
      setError(
        companyCreated
          ? `Company '${trimmed}' was created, but setup completion failed: ${baseMessage}. Open the wizard's Complete step or reload the page to retry.`
          : baseMessage,
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
          <ErrorBanner variant="section" severity="error" title="Could not skip the wizard" description={error} />
        )}

        <Button type="submit" disabled={loading} className="w-full">
          {loading ? 'Setting up...' : 'Complete Setup'}
        </Button>
      </form>
    </div>
  )
}
