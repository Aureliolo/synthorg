import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SkipWizardForm } from './SkipWizardForm'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useSetupStore } from '@/stores/setup'
import { useToastStore } from '@/stores/toast'
import { MiniOrgChart } from './MiniOrgChart'
import { SetupSummary } from './SetupSummary'
import { CheckCircle } from 'lucide-react'

export function CompleteStep() {
  const navigate = useNavigate()
  const [confirmOpen, setConfirmOpen] = useState(false)

  const companyResponse = useSetupWizardStore((s) => s.companyResponse)
  const agents = useSetupWizardStore((s) => s.agents)
  const providers = useSetupWizardStore((s) => s.providers)
  const currency = useSetupWizardStore((s) => s.currency)
  const completing = useSetupWizardStore((s) => s.completing)
  const completionError = useSetupWizardStore((s) => s.completionError)
  const completionWarning = useSetupWizardStore((s) => s.completionWarning)
  const wizardCompleteSetup = useSetupWizardStore((s) => s.completeSetup)

  const finishAndNavigate = useCallback(() => {
    useSetupStore.setState({ setupComplete: true })
    useToastStore.getState().add({
      variant: 'success',
      title: `Setup complete! Welcome to ${companyResponse?.company_name ?? 'your organization'}.`,
    })
    // Surface the post-setup guidance card on the dashboard.  The flag
    // lives in localStorage so the card stays dismissible across
    // reloads and is read by ``PostSetupGuidanceCard`` host components.
    try {
      window.localStorage.setItem('synthorg.firstRun', '1')
    } catch {
      // localStorage may be disabled (private mode); the guidance card
      // simply won't surface in that case.  Setup completion proceeds.
    }
    setConfirmOpen(false)
    void navigate('/')
  }, [companyResponse, navigate])

  const handleComplete = useCallback(async () => {
    try {
      await wizardCompleteSetup()
    } catch {
      // Error stored in completionError by the store action and rendered below.
      return
    }
    // Hold the wizard open if the backend reported a non-fatal warning
    // (e.g. embedder auto-selection failed; provider health degraded
    // mid-setup). The user clicks ``Continue to dashboard`` after
    // reading the notice so a half-configured runtime does not
    // silently land on the dashboard.
    if (useSetupWizardStore.getState().completionWarning !== null) {
      setConfirmOpen(false)
      return
    }
    finishAndNavigate()
  }, [wizardCompleteSetup, finishAndNavigate])

  if (!companyResponse) {
    return <SkipWizardForm />
  }

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Review & Complete</h2>
        <p className="text-sm text-muted-foreground">
          Review your organization before launching.
        </p>
      </div>

      {/* Mini org chart */}
      <MiniOrgChart agents={agents} />

      {/* Summary */}
      <SetupSummary
        companyResponse={companyResponse}
        agents={agents}
        providers={providers}
        currency={currency}
      />

      {completionError && (
        <ErrorBanner
          variant="section"
          severity="error"
          title="Could not complete setup"
          description={
            // Append a contextual help line for users hitting this
            // repeatedly: setup may have already completed in a
            // previous attempt and the wizard just hasn't observed
            // the new state. A page refresh confirms.
            `${completionError} If you see this repeatedly, setup may have already completed; refresh the page to confirm.`
          }
          onRetry={() => void handleComplete()}
        />
      )}

      {completionWarning && !completionError && (
        // Non-fatal warning surface: setup did persist, but the
        // backend reported a runtime caveat (embedder auto-selection
        // failed, provider health degraded mid-setup). Holding the
        // wizard open here avoids the previous behaviour of
        // navigating to a half-configured dashboard with no notice.
        <ErrorBanner
          variant="section"
          severity="warning"
          title="Setup complete with a warning"
          description={`${completionWarning} You can continue to the dashboard and resolve this from Settings.`}
        />
      )}

      {/* Complete button */}
      {completionWarning && !completionError ? (
        <Button
          onClick={finishAndNavigate}
          className="w-full gap-2"
          size="lg"
        >
          <CheckCircle className="size-4" />
          Continue to dashboard
        </Button>
      ) : (
        <Button
          onClick={() => setConfirmOpen(true)}
          disabled={completing}
          className="w-full gap-2"
          size="lg"
        >
          <CheckCircle className="size-4" />
          {completing ? 'Completing Setup...' : 'Complete Setup'}
        </Button>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Launch your organization?"
        description="This will start all configured agents and complete the setup process."
        confirmLabel="Launch"
        onConfirm={handleComplete}
        loading={completing}
      />
    </div>
  )
}
