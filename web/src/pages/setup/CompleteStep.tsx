import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SkipWizardForm } from './SkipWizardForm'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useSetupStore } from '@/stores/setup'
import { useToastStore } from '@/stores/toast'
import { markFirstRunPending } from '@/utils/first-run'
import { MiniOrgChart } from './MiniOrgChart'
import { SetupSummary } from './SetupSummary'
import { CheckCircle } from 'lucide-react'
import type { SetupCompanyResponse } from '@/api/types/setup'

interface CompleteStepActions {
  confirmOpen: boolean
  setConfirmOpen: (open: boolean) => void
  finishAndNavigate: () => void
  handleComplete: () => Promise<void>
}

function useCompleteStepActions(
  companyResponse: SetupCompanyResponse | null,
  wizardCompleteSetup: () => Promise<void>,
): CompleteStepActions {
  const navigate = useNavigate()
  const [confirmOpen, setConfirmOpen] = useState(false)

  const finishAndNavigate = useCallback(() => {
    useSetupStore.setState({ setupComplete: true })
    useToastStore.getState().add({
      variant: 'success',
      title: `Setup complete! Welcome to ${companyResponse?.company_name ?? 'your organization'}.`,
    })
    // Surface the post-setup guidance card on the dashboard (read by
    // ``PostSetupGuidanceCard`` via the shared first-run flag).
    markFirstRunPending()
    setConfirmOpen(false)
    void navigate('/')
  }, [companyResponse, navigate])

  const handleComplete = useCallback(async () => {
    // Store owns the error UX: ``completeSetup`` sets
    // ``completionError`` and does not throw, so the caller must not
    // wrap it in try/catch. Branch off store state after it resolves.
    await wizardCompleteSetup()
    const wizardState = useSetupWizardStore.getState()
    if (wizardState.completionError !== null) {
      // Failure: the error is rendered below from store state. Keep
      // the confirm dialog as-is so the user can retry.
      return
    }
    // Hold the wizard open if the backend reported a non-fatal warning
    // (e.g. embedder auto-selection failed; provider health degraded
    // mid-setup). The user clicks ``Continue to dashboard`` after
    // reading the notice so a half-configured runtime does not
    // silently land on the dashboard.
    if (wizardState.completionWarning !== null) {
      setConfirmOpen(false)
      return
    }
    finishAndNavigate()
  }, [wizardCompleteSetup, finishAndNavigate])

  return { confirmOpen, setConfirmOpen, finishAndNavigate, handleComplete }
}

interface CompleteStepFooterProps {
  completionError: string | null
  completionWarning: string | null
  completing: boolean
  confirmOpen: boolean
  setConfirmOpen: (open: boolean) => void
  finishAndNavigate: () => void
  handleComplete: () => Promise<void>
}

function CompleteStepFooter({
  completionError,
  completionWarning,
  completing,
  confirmOpen,
  setConfirmOpen,
  finishAndNavigate,
  handleComplete,
}: CompleteStepFooterProps) {
  const showWarningOnly = Boolean(completionWarning) && !completionError
  return (
    <>
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

      {showWarningOnly && (
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

      {showWarningOnly ? (
        <Button onClick={finishAndNavigate} className="w-full gap-2" size="lg">
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
        description="This starts all configured agents and finishes setup. Agents may begin working and incurring provider costs immediately, and you can't return to the setup wizard afterwards."
        confirmLabel="Launch"
        onConfirm={handleComplete}
        loading={completing}
      />
    </>
  )
}

export function CompleteStep() {
  const companyResponse = useSetupWizardStore((s) => s.companyResponse)
  const agents = useSetupWizardStore((s) => s.agents)
  const providers = useSetupWizardStore((s) => s.providers)
  const currency = useSetupWizardStore((s) => s.currency)
  const completing = useSetupWizardStore((s) => s.completing)
  const completionError = useSetupWizardStore((s) => s.completionError)
  const completionWarning = useSetupWizardStore((s) => s.completionWarning)
  const wizardCompleteSetup = useSetupWizardStore((s) => s.completeSetup)

  const { confirmOpen, setConfirmOpen, finishAndNavigate, handleComplete } =
    useCompleteStepActions(companyResponse, wizardCompleteSetup)

  if (!companyResponse) {
    // Reaching Complete without a generated company means the Company step
    // was never applied (e.g. direct navigation to /setup/complete, or the
    // template was never confirmed). Explain that before offering the
    // minimal skip-the-wizard path so the empty form isn't a surprise.
    return (
      <div className="space-y-section-gap">
        <ErrorBanner
          variant="section"
          severity="info"
          title="No company configured yet"
          description="You reached the final step before a company was generated. Name your organisation below to finish with defaults, or go back to apply a template."
        />
        <SkipWizardForm />
      </div>
    )
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

      <CompleteStepFooter
        completionError={completionError}
        completionWarning={completionWarning}
        completing={completing}
        confirmOpen={confirmOpen}
        setConfirmOpen={setConfirmOpen}
        finishAndNavigate={finishAndNavigate}
        handleComplete={handleComplete}
      />
    </div>
  )
}
