import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams, type NavigateFunction } from 'react-router'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { AnimatedPresence } from '@/components/ui/animated-presence'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useToastStore } from '@/stores/toast'
import type { WizardStep } from '@/stores/setup-wizard'
import { WizardProgress } from './WizardProgress'
import { WizardNavigation } from './WizardNavigation'
import { WizardSkeleton } from './WizardSkeleton'
import { AccountStep } from './AccountStep'
import { WizardModeStep } from './WizardModeStep'
import { TemplateStep } from './TemplateStep'
import { CompanyStep } from './CompanyStep'
import { ProvidersStep } from './ProvidersStep'
import { AgentsStep } from './AgentsStep'
import { ThemeStep } from './ThemeStep'
import { CompleteStep } from './CompleteStep'

const STEP_COMPONENTS: Record<WizardStep, React.ComponentType> = {
  account: AccountStep,
  mode: WizardModeStep,
  template: TemplateStep,
  company: CompanyStep,
  providers: ProvidersStep,
  agents: AgentsStep,
  theme: ThemeStep,
  complete: CompleteStep,
}

/** Steps hidden from the progress bar (pre-wizard gates). */
const HIDDEN_PROGRESS_STEPS = new Set<WizardStep>(['mode'])

const GENERIC_NEXT_DISABLED_REASON =
  'Complete the required fields on this step to continue.'

interface StepLoadingFlags {
  providersLoading: boolean
  presetsLoading: boolean
  probing: boolean
  agentsLoading: boolean
  companyLoading: boolean
}

/**
 * Per-step loading captions. When the step is still fetching its own
 * data, say so specifically ("Waiting for providers to load...") rather
 * than the generic "complete the required fields" line, which would
 * wrongly imply the operator forgot to fill something in. Each entry
 * returns null when the step is loaded so the caller falls back to the
 * generic copy.
 */
const STEP_LOADING_REASONS: Partial<
  Record<WizardStep, (flags: StepLoadingFlags) => string | null>
> = {
  providers: (f) => {
    if (f.providersLoading || f.presetsLoading) return 'Waiting for providers to load...'
    return f.probing ? 'Probing providers...' : null
  },
  agents: (f) => (f.agentsLoading ? 'Waiting for agents to load...' : null),
  company: (f) => (f.companyLoading ? 'Applying the template...' : null),
}

function deriveNextDisabledReason(
  currentStep: WizardStep,
  flags: StepLoadingFlags,
): string {
  return STEP_LOADING_REASONS[currentStep]?.(flags) ?? GENERIC_NEXT_DISABLED_REASON
}

/**
 * Resolve the disabled-Next caption for the current step, reading the
 * per-step loading flags from the store. Returns null when the step is
 * already complete (Next is enabled, no caption needed).
 */
function useNextDisabledReason(
  currentStep: WizardStep,
  stepComplete: boolean,
): string | null {
  const providersLoading = useSetupWizardStore((s) => s.providersLoading)
  const presetsLoading = useSetupWizardStore((s) => s.presetsLoading)
  const probing = useSetupWizardStore((s) => s.probing)
  const agentsLoading = useSetupWizardStore((s) => s.agentsLoading)
  const companyLoading = useSetupWizardStore((s) => s.companyLoading)
  if (stepComplete) return null
  return deriveNextDisabledReason(currentStep, {
    providersLoading,
    presetsLoading,
    probing,
    agentsLoading,
    companyLoading,
  })
}

function isWizardStep(value: string, stepOrder: readonly WizardStep[]): value is WizardStep {
  return stepOrder.includes(value as WizardStep)
}

/**
 * E4 re-entry guidance: if the user reloads mid-wizard AFTER the company
 * was created but BEFORE setup was fully marked complete, surface a
 * one-shot toast pointing them at the Complete step so they understand
 * the partial state and can resume. Without this they land on whatever
 * step the merge() clamped them to and the only signal that "the company
 * exists" is implicit in the already-pre-populated form state. The toast
 * must NOT fire when the company is created during the current session --
 * capturing the initial mount-time value distinguishes "hydrated from
 * persisted state" (re-entry) from "set to present during this session"
 * (newly created).
 */
function useWizardReEntryToast(
  companyPresent: boolean,
  completeStepDone: boolean,
  stepOrder: readonly WizardStep[],
): void {
  const reEntryToastShownRef = useRef(false)
  // Capture the mount-time value via useRef's initialiser so the render
  // body stays free of side effects (no null-sentinel mutation): useRef
  // only honours the argument on the first render, which is exactly the
  // "did the company already exist when this wizard mounted" signal.
  const companyExistedAtMountRef = useRef(companyPresent)
  useEffect(() => {
    if (reEntryToastShownRef.current) return
    if (!companyExistedAtMountRef.current) return
    if (!companyPresent) return
    if (completeStepDone) return
    if (!stepOrder.includes('complete')) return
    reEntryToastShownRef.current = true
    useToastStore.getState().add({
      variant: 'info',
      title: 'Resume setup',
      description: 'Your company was created in a previous session. Finish setup from the Complete step.',
    })
  }, [companyPresent, completeStepDone, stepOrder])
}

interface WizardUrlSyncArgs {
  urlStep: string | undefined
  stepOrder: readonly WizardStep[]
  canNavigateTo: (step: WizardStep) => boolean
  setStep: (step: WizardStep) => void
  stepsCompleted: Record<WizardStep, boolean>
  navigate: NavigateFunction
}

/** Keep the store's current step in sync with the `:step` URL param. */
function useWizardUrlSync({
  urlStep,
  stepOrder,
  canNavigateTo,
  setStep,
  stepsCompleted,
  navigate,
}: WizardUrlSyncArgs): void {
  useEffect(() => {
    if (!urlStep) {
      void navigate(`/setup/${stepOrder[0]}`, { replace: true })
      return
    }
    if (isWizardStep(urlStep, stepOrder)) {
      if (canNavigateTo(urlStep)) {
        setStep(urlStep)
      } else {
        const firstIncomplete = stepOrder.find((s) => !stepsCompleted[s])
        const target = firstIncomplete ?? stepOrder[0]
        useToastStore.getState().add({
          variant: 'warning',
          title: 'Previous steps not complete',
          description: `Finish the earlier steps before jumping to ${urlStep}.`,
        })
        void navigate(`/setup/${target}`, { replace: true })
      }
    } else {
      // Invalid step name in URL -- redirect to first step and tell the user.
      useToastStore.getState().add({
        variant: 'warning',
        title: 'Unknown setup step',
        description: `"${urlStep}" is not a valid step. Returning to ${stepOrder[0]}.`,
      })
      void navigate(`/setup/${stepOrder[0]}`, { replace: true })
    }
  }, [urlStep, stepOrder, canNavigateTo, setStep, stepsCompleted, navigate])
}

interface WizardStepNavigation {
  handleStepClick: (step: WizardStep) => void
  handleBack: () => void
  handleNext: () => void
}

function useWizardStepNavigation(
  currentStep: WizardStep,
  stepOrder: readonly WizardStep[],
  canNavigateTo: (step: WizardStep) => boolean,
  navigate: NavigateFunction,
): WizardStepNavigation {
  const handleStepClick = useCallback(
    (step: WizardStep) => {
      if (!canNavigateTo(step)) return
      void navigate(`/setup/${step}`)
    },
    [canNavigateTo, navigate],
  )

  const handleBack = useCallback(() => {
    const idx = stepOrder.indexOf(currentStep)
    if (idx > 0) {
      void navigate(`/setup/${stepOrder[idx - 1]}`)
    }
  }, [currentStep, stepOrder, navigate])

  const handleNext = useCallback(() => {
    const idx = stepOrder.indexOf(currentStep)
    if (idx < stepOrder.length - 1) {
      void navigate(`/setup/${stepOrder[idx + 1]}`)
    }
  }, [currentStep, stepOrder, navigate])

  return { handleStepClick, handleBack, handleNext }
}

export function WizardShell() {
  const navigate = useNavigate()
  const { step: urlStep } = useParams<{ step?: string }>()

  const currentStep = useSetupWizardStore((s) => s.currentStep)
  const stepOrder = useSetupWizardStore((s) => s.stepOrder)
  const stepsCompleted = useSetupWizardStore((s) => s.stepsCompleted)
  const stepsNeedRevalidation = useSetupWizardStore((s) => s.stepsNeedRevalidation)
  const setStep = useSetupWizardStore((s) => s.setStep)
  const canNavigateTo = useSetupWizardStore((s) => s.canNavigateTo)
  const companyResponse = useSetupWizardStore((s) => s.companyResponse)
  const stepComplete = stepsCompleted[currentStep]
  const nextDisabledReason = useNextDisabledReason(currentStep, stepComplete)

  useWizardReEntryToast(companyResponse !== null, stepsCompleted.complete, stepOrder)
  useWizardUrlSync({ urlStep, stepOrder, canNavigateTo, setStep, stepsCompleted, navigate })
  const { handleStepClick, handleBack, handleNext } = useWizardStepNavigation(
    currentStep,
    stepOrder,
    canNavigateTo,
    navigate,
  )

  // Steps shown in the progress bar (filter out hidden steps)
  const progressSteps = useMemo(
    () => stepOrder.filter((s) => !HIDDEN_PROGRESS_STEPS.has(s)),
    [stepOrder],
  )

  if (!urlStep) {
    return <WizardSkeleton />
  }

  const StepComponent = STEP_COMPONENTS[currentStep]
  const showProgress = !HIDDEN_PROGRESS_STEPS.has(currentStep)

  return (
    <div className="flex min-h-screen flex-col items-center bg-background">
      <div className="w-full max-w-4xl flex-1 px-4 py-8">
        {/* Progress bar (hidden for mode selection step) */}
        {showProgress && (
          <div className="mb-8">
            <WizardProgress
              stepOrder={progressSteps}
              currentStep={currentStep}
              stepsCompleted={stepsCompleted}
              stepsNeedRevalidation={stepsNeedRevalidation}
              canNavigateTo={canNavigateTo}
              onStepClick={handleStepClick}
            />
          </div>
        )}

        {/* Step content */}
        <ErrorBoundary level="page">
          <AnimatedPresence routeKey={currentStep}>
            <StepComponent />
          </AnimatedPresence>
        </ErrorBoundary>

        {/* Navigation (hidden for mode selection step -- it advances on click) */}
        {showProgress && (
          <div className="mt-8">
            <WizardNavigation
              stepOrder={stepOrder}
              currentStep={currentStep}
              onBack={handleBack}
              onNext={handleNext}
              nextDisabled={!stepComplete}
              nextDisabledReason={nextDisabledReason}
            />
          </div>
        )}
      </div>
    </div>
  )
}
