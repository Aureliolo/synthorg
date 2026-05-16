import { AlertTriangle, Check } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import type { WizardStep } from '@/stores/setup-wizard'

interface StepConfig {
  readonly key: WizardStep
  readonly label: string
}

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

interface StepIndicatorProps {
  step: StepConfig
  index: number
  isActive: boolean
  isComplete: boolean
  needsRevalidation: boolean
  isAccessible: boolean
  isLast: boolean
  onStepClick: (step: WizardStep) => void
}

function StepIndicator({
  step,
  index,
  isActive,
  isComplete,
  needsRevalidation,
  isAccessible,
  isLast,
  onStepClick,
}: StepIndicatorProps) {
  // Revalidation only renders when the step is also complete: an
  // incomplete step's empty circle already telegraphs "you have work
  // to do here", so the warning glyph would be redundant noise. The
  // active step never carries the warning either, so the user does
  // not see the alert on the screen they are about to fix.
  const showWarning = isComplete && needsRevalidation && !isActive
  return (
    <div className="flex items-center">
      <button
        type="button"
        onClick={() => onStepClick(step.key)}
        disabled={!isAccessible}
        aria-current={isActive ? 'step' : undefined}
        aria-describedby={showWarning ? `${step.key}-needs-revalidation` : undefined}
        className={cn(
          'flex flex-col items-center gap-1',
          FOCUS_RING,
          'rounded-md px-2 py-1 transition-colors',
          isAccessible && !isActive && 'cursor-pointer hover:bg-card-hover',
          !isAccessible && 'cursor-not-allowed opacity-50',
        )}
      >
        <div
          className={cn(
            'flex size-8 items-center justify-center rounded-full text-xs font-semibold transition-colors',
            isActive && 'bg-accent text-accent-foreground',
            isComplete && !isActive && !showWarning && 'bg-success/20 text-success',
            showWarning && 'bg-warning/20 text-warning',
            !isActive && !isComplete && 'bg-card text-muted-foreground border border-border',
          )}
        >
          {showWarning ? (
            <AlertTriangle className="size-4" aria-hidden="true" />
          ) : isComplete ? (
            <Check className="size-4" aria-hidden="true" />
          ) : (
            index + 1
          )}
        </div>
        <span
          className={cn(
            'text-compact',
            isActive && 'font-semibold text-foreground',
            !isActive && !showWarning && 'text-muted-foreground',
            showWarning && 'text-warning',
          )}
        >
          {step.label}
        </span>
        {showWarning && (
          <span id={`${step.key}-needs-revalidation`} className="sr-only">
            Needs review: upstream changes may have invalidated this step.
          </span>
        )}
      </button>
      {!isLast && (
        <div
          className={cn(
            'mx-1 h-px w-8',
            isComplete ? 'bg-success/40' : 'bg-border',
          )}
          aria-hidden="true"
        />
      )}
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
  const steps: StepConfig[] = stepOrder.map((key) => ({
    key,
    label: STEP_LABELS[key],
  }))

  return (
    <nav aria-label="Setup progress" className="flex items-center justify-center gap-0">
      {steps.map((step, index) => (
        <StepIndicator
          key={step.key}
          step={step}
          index={index}
          isActive={step.key === currentStep}
          isComplete={stepsCompleted[step.key]}
          needsRevalidation={stepsNeedRevalidation[step.key]}
          isAccessible={canNavigateTo(step.key)}
          isLast={index === steps.length - 1}
          onStepClick={onStepClick}
        />
      ))}
    </nav>
  )
}
