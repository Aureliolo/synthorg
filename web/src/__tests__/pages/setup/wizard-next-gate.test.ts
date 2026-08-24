import { renderHook } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { WizardStep } from '@/stores/setup-wizard'
import { useWizardNextGate } from '@/pages/setup/wizard-next-gate'

function completeStep(step: WizardStep): Record<WizardStep, boolean> {
  return { ...useSetupWizardStore.getState().stepsCompleted, [step]: true }
}

function incompleteStep(step: WizardStep): Record<WizardStep, boolean> {
  return { ...useSetupWizardStore.getState().stepsCompleted, [step]: false }
}

describe('useWizardNextGate', () => {
  it('enables Next once the step reports itself complete', () => {
    useSetupWizardStore.setState({ stepsCompleted: completeStep('agents') })
    const { result } = renderHook(() => useWizardNextGate('agents'))
    expect(result.current.disabled).toBe(false)
    expect(result.current.reason).toBeNull()
  })

  it('disables Next with the generic caption when the step is incomplete', () => {
    useSetupWizardStore.setState({
      stepsCompleted: incompleteStep('agents'),
      agentsLoading: false,
    })
    const { result } = renderHook(() => useWizardNextGate('agents'))
    expect(result.current.disabled).toBe(true)
    expect(result.current.reason).toMatch(/complete the required fields/i)
  })

  it('says what it is waiting for while the step is still loading', () => {
    useSetupWizardStore.setState({
      stepsCompleted: incompleteStep('agents'),
      agentsLoading: true,
    })
    const { result } = renderHook(() => useWizardNextGate('agents'))
    expect(result.current.disabled).toBe(true)
    expect(result.current.reason).toMatch(/waiting for agents to load/i)
  })
})
