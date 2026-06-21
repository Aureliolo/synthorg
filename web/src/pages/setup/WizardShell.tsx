import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams, type NavigateFunction } from 'react-router'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { AnimatedPresence } from '@/components/ui/animated-presence'
import { ToastContainer } from '@/components/ui/toast'
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

/** Per-step browser-tab titles (the wizard renders outside AppLayout). */
const STEP_TITLES: Record<WizardStep, string> = {
  account: 'Account',
  mode: 'Choose setup mode',
  template: 'Pick a template',
  company: 'Company details',
  providers: 'Set up providers',
  agents: 'Configure agents',
  theme: 'Choose a theme',
  complete: 'Review & complete',
}

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
  // The effect re-fires whenever stepOrder / canNavigateTo / stepsCompleted
  // change identity, even while `urlStep` stays on the same blocked or
  // unknown value. Without this guard each re-fire stacks an identical
  // toast. Keyed on the redirect reason + step so a genuinely new problem
  // still notifies.
  const lastToastKeyRef = useRef<string | null>(null)
  const toastOnce = useCallback(
    (key: string, toast: { title: string; description: string }) => {
      if (lastToastKeyRef.current === key) return
      lastToastKeyRef.current = key
      useToastStore.getState().add({ variant: 'warning', ...toast })
    },
    [],
  )
  useEffect(() => {
    if (!urlStep) {
      void navigate(`/setup/${stepOrder[0]}`, { replace: true })
      return
    }
    if (isWizardStep(urlStep, stepOrder)) {
      if (canNavigateTo(urlStep)) {
        lastToastKeyRef.current = null
        setStep(urlStep)
      } else {
        const firstIncomplete = stepOrder.find((s) => !stepsCompleted[s])
        const target = firstIncomplete ?? stepOrder[0]
        toastOnce(`incomplete:${urlStep}`, {
          title: 'Previous steps not complete',
          description: `Finish the earlier steps before jumping to ${urlStep}.`,
        })
        void navigate(`/setup/${target}`, { replace: true })
      }
    } else {
      // Invalid step name in URL -- redirect to first step and tell the user.
      toastOnce(`unknown:${urlStep}`, {
        title: 'Unknown setup step',
        description: `"${urlStep}" is not a valid step. Returning to ${stepOrder[0]}.`,
      })
      void navigate(`/setup/${stepOrder[0]}`, { replace: true })
    }
  }, [urlStep, stepOrder, canNavigateTo, setStep, stepsCompleted, navigate, toastOnce])
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

/**
 * Drive the browser-tab title from the active step (the wizard renders
 * outside AppLayout, which owns titles for the rest of the app) and reset
 * the content scroll position to the top whenever the step changes, so a
 * long step never leaves the next step scrolled half-way down.
 */
function useWizardStepChrome(
  currentStep: WizardStep,
  scrollRef: React.RefObject<HTMLDivElement | null>,
): void {
  useEffect(() => {
    document.title = `${STEP_TITLES[currentStep]} · Setup · SynthOrg`
  }, [currentStep])
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [currentStep, scrollRef])
}

export function WizardShell() {
  const navigate = useNavigate()
  const { step: urlStep } = useParams<{ step?: string }>()
  const scrollRef = useRef<HTMLDivElement>(null)

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
  useWizardStepChrome(currentStep, scrollRef)
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
  // Navigation renders on every step (including mode) so a step reached
  // from an earlier step keeps a Back affordance; the mode step hides
  // its Next button because selecting a mode auto-advances.
  const isModeStep = currentStep === 'mode'

  return (
    // Fixed-height shell: the shell owns the viewport height and hides its
    // own overflow; only the content column scrolls, so the navigation
    // stays pinned at the bottom instead of forcing a scroll-to-find-Next.
    <div className="flex h-dvh flex-col items-center overflow-hidden bg-background">
      <div className="flex w-full max-w-4xl flex-1 flex-col overflow-hidden px-4">
        <h1 className="sr-only">SynthOrg setup wizard</h1>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto py-8">
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
        </div>

        {/* Navigation: pinned outside the scroll region (shrink-0) so it is
            always reachable. Shown on every step; the mode step hides Next
            (selecting a mode auto-advances) but keeps Back so a user who
            reached mode from the account step can return. */}
        <div className="shrink-0 bg-background pb-4">
          <WizardNavigation
            stepOrder={stepOrder}
            currentStep={currentStep}
            onBack={handleBack}
            onNext={handleNext}
            nextDisabled={!stepComplete}
            nextDisabledReason={nextDisabledReason}
            hideNext={isModeStep}
          />
        </div>
      </div>

      {/* Wizard renders outside AppLayout, so it mounts its own toast
          renderer; without this, setup error/success toasts had no host. */}
      <ToastContainer />
    </div>
  )
}
