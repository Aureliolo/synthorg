import { useEffect } from 'react'
import { useSetupWizardStore, type WizardStep } from '@/stores/setup-wizard'

/**
 * Sync a step's completion flag in the wizard store from a per-step
 * validation boolean computed in the component. Centralises the
 * `markStepComplete / markStepIncomplete` effect that the per-step
 * pages (Company, Providers, Agents) previously each wrote by hand.
 */
export function useStepCompletionSync(step: WizardStep, isValid: boolean): void {
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)
  const markStepIncomplete = useSetupWizardStore((s) => s.markStepIncomplete)
  useEffect(() => {
    if (isValid) {
      markStepComplete(step)
    } else {
      markStepIncomplete(step)
    }
  }, [step, isValid, markStepComplete, markStepIncomplete])
}
