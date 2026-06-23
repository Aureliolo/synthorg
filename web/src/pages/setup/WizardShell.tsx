import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, type NavigateFunction } from 'react-router'
import { cn } from '@/lib/utils'
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
  stepsCompleted: Record<WizardStep, boolean>,
  statusReconciled: boolean,
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
    // Wait for the backend reconcile before naming the resume step: pre-
    // reconcile `stepsCompleted` has providers/company still false, so the
    // toast would point at an already-finished step (the "land on Providers"
    // bug). Once reconciled, the live map reflects what the server has.
    if (!statusReconciled) return
    reEntryToastShownRef.current = true
    // Point the operator at the actual first-incomplete step rather than
    // "Complete": providers / agents rehydrate empty and re-block on reload,
    // so canNavigateTo('complete') is false until they re-verify. Telling them
    // to "finish from Complete" would be unfollowable.
    const firstIncomplete = stepOrder.find((s) => !stepsCompleted[s])
    const target = firstIncomplete ?? 'complete'
    useToastStore.getState().add({
      variant: 'info',
      title: 'Resume setup',
      description:
        `Your company was created in a previous session. Continue from the ${STEP_TITLES[target]} step.`,
    })
  }, [companyPresent, completeStepDone, stepOrder, stepsCompleted, statusReconciled])
}

interface WizardUrlSyncArgs {
  urlStep: string | undefined
  stepOrder: readonly WizardStep[]
  canNavigateTo: (step: WizardStep) => boolean
  setStep: (step: WizardStep) => void
  stepsCompleted: Record<WizardStep, boolean>
  navigate: NavigateFunction
  statusReconciled: boolean
}

/**
 * Reconcile finished steps from the backend once on mount so a reload does not
 * bounce the operator backwards past steps the server already has.
 */
function useBackendReconcileOnMount(): void {
  const reconcile = useSetupWizardStore((s) => s.reconcileCompletionFromBackend)
  useEffect(() => {
    void reconcile()
  }, [reconcile])
}

/** Progress-bar steps: the full order minus steps hidden from the bar. */
function useProgressSteps(stepOrder: readonly WizardStep[]): WizardStep[] {
  return useMemo(
    () => stepOrder.filter((s) => !HIDDEN_PROGRESS_STEPS.has(s)),
    [stepOrder],
  )
}

/** Keep the store's current step in sync with the `:step` URL param. */
function useWizardUrlSync({
  urlStep,
  stepOrder,
  canNavigateTo,
  setStep,
  stepsCompleted,
  navigate,
  statusReconciled,
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
      // Resume at the first incomplete step rather than always step 0, so a
      // mid-setup reload (or re-login) drops the operator back where they were
      // instead of replaying the mode picker / Template they already finished.
      // Hold until the backend reconcile lands: choosing pre-reconcile would
      // pick a step the server has already finished (the "land on Providers"
      // bug) and the URL would then pin the operator there.
      if (!statusReconciled) return
      const resumeStep = stepOrder.find((s) => !stepsCompleted[s]) ?? stepOrder[0]
      void navigate(`/setup/${resumeStep}`, { replace: true })
      return
    }
    if (isWizardStep(urlStep, stepOrder)) {
      if (canNavigateTo(urlStep)) {
        lastToastKeyRef.current = null
        setStep(urlStep)
      } else if (!statusReconciled) {
        // Backend reconcile still pending: the requested step may become
        // reachable once finished steps are re-marked. Hold (no bounce, no
        // toast); this effect re-runs when stepsCompleted / statusReconciled
        // update.
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
  }, [urlStep, stepOrder, canNavigateTo, setStep, stepsCompleted, navigate, toastOnce, statusReconciled])
}

/** Re-entry toast + URL/step reconciliation for a resumed wizard. */
function useWizardResume(
  args: WizardUrlSyncArgs & { companyPresent: boolean },
): void {
  useWizardReEntryToast(
    args.companyPresent,
    args.stepsCompleted.complete,
    args.stepOrder,
    args.stepsCompleted,
    args.statusReconciled,
  )
  useWizardUrlSync(args)
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

interface ScrollHints {
  top: boolean
  bottom: boolean
}

/**
 * Dynamic scroll affordance for the wizard's scrollbar-free content column.
 * Reports whether content is clipped above / below the current scroll position
 * so the shell can fade in a hint at that edge -- a smarter, quieter signal
 * than an always-visible scrollbar. Recomputed on scroll, on container/content
 * resize, and whenever the step changes (content height + scroll reset).
 */
function useScrollAffordance(
  scrollRef: React.RefObject<HTMLDivElement | null>,
  contentRef: React.RefObject<HTMLDivElement | null>,
  currentStep: WizardStep,
): ScrollHints {
  const [hints, setHints] = useState<ScrollHints>({ top: false, bottom: false })
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => {
      const maxScroll = el.scrollHeight - el.clientHeight
      const top = el.scrollTop > 1
      const bottom = el.scrollTop < maxScroll - 1
      setHints((prev) => (prev.top === top && prev.bottom === bottom ? prev : { top, bottom }))
    }
    // Defer the first measure out of the effect body so it reads post-layout
    // metrics (and never sets state synchronously during commit).
    const raf = requestAnimationFrame(update)
    el.addEventListener('scroll', update, { passive: true })
    const observer = new ResizeObserver(update)
    observer.observe(el)
    if (contentRef.current) observer.observe(contentRef.current)
    return () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('scroll', update)
      observer.disconnect()
    }
  }, [scrollRef, contentRef, currentStep])
  return hints
}

/** Scrollbar-free content column with dynamic edge-fade scroll hints. */
function WizardScrollArea({
  scrollRef,
  contentRef,
  hints,
  center,
  children,
}: {
  scrollRef: React.RefObject<HTMLDivElement | null>
  contentRef: React.RefObject<HTMLDivElement | null>
  hints: ScrollHints
  /** Vertically centre the content (focused choice screens like the mode step)
   *  rather than top-aligning it (content steps, so the heading stays put and
   *  doesn't jump as the body grows). */
  center: boolean
  children: React.ReactNode
}) {
  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scrollRef} className="no-scrollbar flex h-full flex-col overflow-y-auto">
        <div ref={contentRef} className={cn('w-full py-8', center && 'my-auto')}>
          {children}
        </div>
      </div>
      {/* Dynamic scroll affordance: a quiet fade at any clipped edge, instead
          of an always-visible scrollbar. */}
      <div
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute inset-x-0 top-0 h-8 bg-gradient-to-b from-background to-transparent transition-opacity duration-[var(--so-transition-medium)]',
          hints.top ? 'opacity-100' : 'opacity-0',
        )}
      />
      <div
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-background to-transparent transition-opacity duration-[var(--so-transition-medium)]',
          hints.bottom ? 'opacity-100' : 'opacity-0',
        )}
      />
    </div>
  )
}

export function WizardShell() {
  const navigate = useNavigate()
  const { step: urlStep } = useParams<{ step?: string }>()
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const currentStep = useSetupWizardStore((s) => s.currentStep)
  const stepOrder = useSetupWizardStore((s) => s.stepOrder)
  const stepsCompleted = useSetupWizardStore((s) => s.stepsCompleted)
  const stepsNeedRevalidation = useSetupWizardStore((s) => s.stepsNeedRevalidation)
  const setStep = useSetupWizardStore((s) => s.setStep)
  const canNavigateTo = useSetupWizardStore((s) => s.canNavigateTo)
  const companyResponse = useSetupWizardStore((s) => s.companyResponse)
  const statusReconciled = useSetupWizardStore((s) => s.statusReconciled)
  const stepComplete = stepsCompleted[currentStep]
  const nextDisabledReason = useNextDisabledReason(currentStep, stepComplete)

  useBackendReconcileOnMount()
  useWizardResume({
    urlStep, stepOrder, canNavigateTo, setStep, stepsCompleted, navigate,
    statusReconciled, companyPresent: companyResponse !== null,
  })
  useWizardStepChrome(currentStep, scrollRef)
  const scrollHints = useScrollAffordance(scrollRef, contentRef, currentStep)
  const { handleStepClick, handleBack, handleNext } = useWizardStepNavigation(
    currentStep,
    stepOrder,
    canNavigateTo,
    navigate,
  )

  const progressSteps = useProgressSteps(stepOrder)

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
      <div className="flex w-full max-w-5xl flex-1 flex-col overflow-hidden px-4">
        <h1 className="sr-only">SynthOrg setup wizard</h1>

        {/* Progress bar pinned at the top (outside the scroll region) so the
            stepper stays put while the step content below it centres / scrolls.
            Hidden for the mode selection step. */}
        {showProgress && (
          <div className="shrink-0 pt-8">
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

        <WizardScrollArea scrollRef={scrollRef} contentRef={contentRef} hints={scrollHints} center={isModeStep}>
          <ErrorBoundary level="page">
            <AnimatedPresence routeKey={currentStep}>
              <StepComponent />
            </AnimatedPresence>
          </ErrorBoundary>
        </WizardScrollArea>

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
