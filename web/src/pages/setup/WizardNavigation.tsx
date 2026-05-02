import { ArrowLeft, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { WizardStep } from '@/stores/setup-wizard'

export interface WizardNavigationProps {
  stepOrder: readonly WizardStep[]
  currentStep: WizardStep
  onBack: () => void
  onNext: () => void
  nextDisabled?: boolean
  /**
   * Caption rendered under the Next button when it's disabled. Tells
   * the user WHY they can't advance ("Waiting for providers to load...",
   * "Complete required fields to continue.") so the disabled button
   * isn't a dead end.
   */
  nextDisabledReason?: string | null
  nextLabel?: string
  loading?: boolean
}

export function WizardNavigation({
  stepOrder,
  currentStep,
  onBack,
  onNext,
  nextDisabled,
  nextDisabledReason,
  nextLabel,
  loading,
}: WizardNavigationProps) {
  const rawIdx = stepOrder.indexOf(currentStep)
  const currentIdx = rawIdx === -1 ? 0 : rawIdx
  const isFirst = currentIdx === 0
  const isLast = currentIdx === stepOrder.length - 1

  return (
    <div className="flex flex-col gap-2 border-t border-border px-2 pt-4">
      <div className="flex items-center justify-between">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          disabled={isFirst}
          className="gap-2"
        >
          <ArrowLeft className="size-4" />
          Back
        </Button>
        {!isLast && (
          <Button
            type="button"
            onClick={onNext}
            disabled={nextDisabled || loading}
            className="gap-2"
          >
            {loading ? 'Loading...' : nextLabel ?? 'Next'}
            {!loading && <ArrowRight className="size-4" />}
          </Button>
        )}
      </div>
      {nextDisabled && nextDisabledReason && !isLast && (
        <p className="text-right text-xs text-text-secondary">
          {nextDisabledReason}
        </p>
      )}
    </div>
  )
}
