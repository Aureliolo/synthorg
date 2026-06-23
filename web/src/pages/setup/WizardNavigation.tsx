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

interface NavVisibility {
  showBack: boolean
  showNext: boolean
  showReason: boolean
}

/** Which navigation controls the current step shows. Extracted to keep the
 *  component under the complexity cap. */
function navVisibility(
  currentIdx: number,
  total: number,
  hideNext: boolean | undefined,
  nextDisabled: boolean | undefined,
  nextDisabledReason: string | null | undefined,
): NavVisibility {
  // Relies on the step-order invariant (see navigation.ts): ``complete`` is
  // always the final entry, so the last index is the terminal step.
  const showNext = currentIdx !== total - 1 && !hideNext
  // Gate on showNext (not just !isLast) so the disabled-reason caption never
  // renders orphaned when the Next control itself is hidden.
  const showReason = showNext && Boolean(nextDisabled) && Boolean(nextDisabledReason)
  return { showBack: currentIdx !== 0, showNext, showReason }
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
  // Stable id for the disabled-reason caption so the Next button can associate
  // it via aria-describedby. Only attached when the caption renders.
  const reasonId = useId()
  const { showBack, showNext, showReason } = navVisibility(
    currentIdx,
    stepOrder.length,
    hideNext,
    nextDisabled,
    nextDisabledReason,
  )
  // Render nothing when there is neither a Back nor a Next control (e.g. the
  // first step auto-advances on selection): an empty bar would still draw its
  // top border as a stray divider line.
  if (!showBack && !showNext) return null

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-card">
      <div className="flex items-center justify-between gap-grid-gap">
        {/* Back is omitted entirely on the first step (rather than shown
            disabled), so a dead control never appears where there is nowhere
            to go back to. The empty span preserves the justify-between layout
            so Next stays right-aligned. */}
        {showBack ? (
          <Button type="button" variant="ghost" onClick={onBack} className="gap-2">
            <ArrowLeft className="size-4" aria-hidden="true" />
            Back
          </Button>
        ) : (
          <span />
        )}
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
