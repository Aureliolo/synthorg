import { useId } from 'react'
import { ArrowLeft, ArrowRight, Loader2 } from 'lucide-react'
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
  /**
   * Suppress the Next button (e.g. the mode step auto-advances on
   * selection, so a Next button would be a no-op). Back is still
   * rendered so a step reached from a prior step keeps a Back
   * affordance.
   */
  hideNext?: boolean
}

interface WizardNextButtonProps {
  onNext: () => void
  nextDisabled?: boolean | undefined
  loading?: boolean | undefined
  nextLabel?: string | undefined
  reasonId: string
  showReason: boolean
}

function WizardNextButton({
  onNext,
  nextDisabled,
  loading,
  nextLabel,
  reasonId,
  showReason,
}: WizardNextButtonProps) {
  return (
    <Button
      type="button"
      onClick={onNext}
      disabled={nextDisabled || loading}
      aria-describedby={showReason ? reasonId : undefined}
      className="gap-2"
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
      {loading ? 'Loading...' : nextLabel ?? 'Next'}
      {!loading && <ArrowRight className="size-4" aria-hidden="true" />}
    </Button>
  )
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
  hideNext,
}: WizardNavigationProps) {
  const rawIdx = stepOrder.indexOf(currentStep)
  const currentIdx = rawIdx === -1 ? 0 : rawIdx
  const isFirst = currentIdx === 0
  // Relies on the step-order invariant (see navigation.ts): ``complete`` is
  // always the final entry, so the last index is the terminal step.
  const isLast = currentIdx === stepOrder.length - 1
  const showNext = !isLast && !hideNext
  // Stable id for the disabled-reason caption so the Next button can
  // associate it via aria-describedby. Only attached on the button
  // when the caption is actually rendered.
  const reasonId = useId()
  // Gate on showNext (not just !isLast) so the disabled-reason caption
  // never renders orphaned when the Next control itself is hidden.
  const showReason = showNext && Boolean(nextDisabled) && Boolean(nextDisabledReason)

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-card">
      <div className="flex items-center justify-between gap-grid-gap">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          disabled={isFirst}
          className="gap-2"
          title={isFirst ? 'This is the first step.' : undefined}
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          Back
        </Button>
        {showNext && (
          <WizardNextButton
            onNext={onNext}
            nextDisabled={nextDisabled}
            loading={loading}
            nextLabel={nextLabel}
            reasonId={reasonId}
            showReason={showReason}
          />
        )}
      </div>
      {showReason && (
        <p id={reasonId} className="text-xs text-pretty text-muted-foreground sm:text-right">
          {nextDisabledReason}
        </p>
      )}
    </div>
  )
}
