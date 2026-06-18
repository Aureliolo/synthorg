import { AlertTriangle, Check } from 'lucide-react'
import { InfoTooltip } from '@/components/ui/info-tooltip'
import { cn, FOCUS_RING } from '@/lib/utils'
import type { WizardStep } from '@/stores/setup-wizard'

const REVALIDATION_EXPLANATION =
  'An earlier step changed since you completed this step. Re-visit it to ' +
  'confirm your selections are still valid.'

const STEP_LABELS: Record<WizardStep, string> = {
  account: 'Account',
  mode: 'Mode',
  template: 'Template',
  company: 'Company',
  providers: 'Providers',
  agents: 'Agents',
  theme: 'Theme',
  complete: 'Done',
}

type StepVisual = 'active' | 'warning' | 'complete' | 'pending'

function stepVisual(isActive: boolean, isComplete: boolean, showWarning: boolean): StepVisual {
  if (isActive) return 'active'
  if (showWarning) return 'warning'
  if (isComplete) return 'complete'
  return 'pending'
}

const CIRCLE_CLASS: Record<StepVisual, string> = {
  active: 'bg-accent text-accent-foreground',
  warning: 'bg-warning/20 text-warning',
  complete: 'bg-success/20 text-success',
  pending: 'bg-card text-muted-foreground border border-border',
}

const LABEL_CLASS: Record<StepVisual, string> = {
  active: 'font-semibold text-foreground',
  warning: 'text-warning',
  complete: 'text-muted-foreground',
  pending: 'text-muted-foreground',
}

interface StepView {
  key: WizardStep
  label: string
  index: number
  isActive: boolean
  isAccessible: boolean
  isComplete: boolean
  showWarning: boolean
  visual: StepVisual
  isLast: boolean
}

function StepCircle({ visual, isComplete, index }: { visual: StepVisual; isComplete: boolean; index: number }) {
  return (
    <div
      className={cn(
        'flex size-8 items-center justify-center rounded-full text-xs font-semibold transition-colors',
        CIRCLE_CLASS[visual],
      )}
    >
      {visual === 'warning' ? (
        <AlertTriangle className="size-4" aria-hidden="true" />
      ) : isComplete ? (
        <Check className="size-4" aria-hidden="true" />
      ) : (
        index + 1
      )}
    </div>
  )
}

function StepConnector({ isComplete }: { isComplete: boolean }) {
  return (
    <div
      className={cn('mx-1 h-px w-8', isComplete ? 'bg-success/40' : 'bg-border')}
      aria-hidden="true"
    />
  )
}

function StepCircleSlot({ view }: { view: StepView }) {
  const circle = (
    <StepCircle visual={view.visual} isComplete={view.isComplete} index={view.index} />
  )
  if (!view.showWarning) return circle
  return <InfoTooltip content={REVALIDATION_EXPLANATION}>{circle}</InfoTooltip>
}

function StepIndicator({
  view,
  onStepClick,
}: {
  view: StepView
  onStepClick: (step: WizardStep) => void
}) {
  const { key, label, isActive, isAccessible, isComplete, showWarning, visual, isLast } = view
  return (
    <div className="flex items-center">
      <button
        type="button"
        onClick={() => onStepClick(key)}
        disabled={!isAccessible}
        aria-current={isActive ? 'step' : undefined}
        aria-describedby={showWarning ? `${key}-needs-revalidation` : undefined}
        className={cn(
          'flex flex-col items-center gap-1',
          FOCUS_RING,
          'rounded-md px-2 py-1 transition-colors',
          isAccessible && !isActive && 'cursor-pointer hover:bg-card-hover',
          !isAccessible && 'cursor-not-allowed opacity-50',
        )}
      >
        <StepCircleSlot view={view} />
        <span className={cn('text-compact', LABEL_CLASS[visual])}>{label}</span>
        {showWarning && (
          <span id={`${key}-needs-revalidation`} className="sr-only">
            {REVALIDATION_EXPLANATION}
          </span>
        )}
      </button>
      {!isLast && <StepConnector isComplete={isComplete} />}
    </div>
  )
}

export interface WizardProgressProps {
  stepOrder: readonly WizardStep[]
  currentStep: WizardStep
  stepsCompleted: Record<WizardStep, boolean>
  stepsNeedRevalidation: Record<WizardStep, boolean>
  canNavigateTo: (step: WizardStep) => boolean
  onStepClick: (step: WizardStep) => void
}

export function WizardProgress({
  stepOrder,
  currentStep,
  stepsCompleted,
  stepsNeedRevalidation,
  canNavigateTo,
  onStepClick,
}: WizardProgressProps) {
  return (
    <nav aria-label="Setup progress" className="flex items-center justify-center gap-0">
      {stepOrder.map((key, index) => {
        const isActive = key === currentStep
        const isComplete = stepsCompleted[key]
        // The warning glyph only renders on a complete-but-stale step the
        // user is NOT currently on: an incomplete step's empty circle
        // already signals "work to do", and the active step is the one
        // being fixed, so the alert would be redundant noise there.
        const showWarning = isComplete && stepsNeedRevalidation[key] && !isActive
        const view: StepView = {
          key,
          label: STEP_LABELS[key],
          index,
          isActive,
          isAccessible: canNavigateTo(key),
          isComplete,
          showWarning,
          visual: stepVisual(isActive, isComplete, showWarning),
          isLast: index === stepOrder.length - 1,
        }
        return <StepIndicator key={key} view={view} onStepClick={onStepClick} />
      })}
    </nav>
  )
}
