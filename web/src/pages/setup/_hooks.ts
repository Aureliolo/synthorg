import { useEffect } from 'react'
import { useSetupWizardStore, type WizardStep } from '@/stores/setup-wizard'

export interface StepCompletionSyncOptions {
  /**
   * When ``true``, the sync only ever marks the step complete; an
   * ``isValid === false`` after a previous ``true`` does NOT flip
   * ``stepsCompleted[step]`` back. Used by the Agents step so an
   * upstream Providers edit does not silently uncomplete a step that
   * the user already cleared. The on-page ErrorBanner plus the
   * progress-bar revalidation glyph carry the "needs review" signal
   * instead.
   *
   * Default ``false`` keeps the reactive behaviour the Company and
   * Providers steps rely on (erasing a required field still demotes
   * the step).
   */
  forwardOnly?: boolean
}

/**
 * Sync a step's completion flag in the wizard store from a per-step
 * validation boolean computed in the component. Centralises the
 * `markStepComplete / markStepIncomplete` effect that the per-step
 * pages (Company, Providers, Agents) previously each wrote by hand.
 */
export function useStepCompletionSync(
  step: WizardStep,
  isValid: boolean,
  options: StepCompletionSyncOptions = {},
): void {
  const { forwardOnly = false } = options
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)
  const markStepIncomplete = useSetupWizardStore((s) => s.markStepIncomplete)
  useEffect(() => {
    if (isValid) {
      markStepComplete(step)
    } else if (!forwardOnly) {
      markStepIncomplete(step)
    }
  }, [step, isValid, forwardOnly, markStepComplete, markStepIncomplete])
}

/**
 * Clear a step's revalidation flag once, on mount of the step's
 * component. Pair with ``markStepNeedsRevalidation`` from upstream
 * slices so the warning glyph on the progress bar disappears the
 * moment the user re-enters the step (the on-page banner takes over
 * for any still-broken state).
 */
export function useClearStepRevalidationOnMount(step: WizardStep): void {
  const clearStepRevalidationFlag = useSetupWizardStore(
    (s) => s.clearStepRevalidationFlag,
  )
  useEffect(() => {
    clearStepRevalidationFlag(step)
  }, [step, clearStepRevalidationFlag])
}
