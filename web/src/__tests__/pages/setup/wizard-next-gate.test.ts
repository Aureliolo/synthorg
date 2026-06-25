import { renderHook } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import type { WizardStep } from '@/stores/setup-wizard'
import { useWizardNextGate } from '@/pages/setup/wizard-next-gate'
import type { SetupAgentSummary } from '@/api/types/setup'

function agent(overrides: Partial<SetupAgentSummary> = {}): SetupAgentSummary {
  return {
    name: 'Alice Smith',
    role: 'Developer',
    department: 'engineering',
    level: 'mid',
    model_provider: 'cloud-x',
    model_id: 'cloud-x-large',
    tier: 'medium',
    personality_preset: 'strategic_planner',
    ...overrides,
  }
}

function completeStep(step: WizardStep): Record<WizardStep, boolean> {
  return { ...useSetupWizardStore.getState().stepsCompleted, [step]: true }
}

describe('useWizardNextGate', () => {
  it('disables Next when an agent has no personality', () => {
    useSetupWizardStore.setState({
      agents: [agent(), agent({ personality_preset: null })],
      stepsCompleted: completeStep('agents'),
    })
    const { result } = renderHook(() => useWizardNextGate('agents'))
    expect(result.current.disabled).toBe(true)
    expect(result.current.reason).toMatch(/personality/i)
  })

  it('enables Next once every agent has a personality', () => {
    useSetupWizardStore.setState({
      agents: [agent(), agent({ personality_preset: 'pragmatic_builder' })],
      stepsCompleted: completeStep('agents'),
    })
    const { result } = renderHook(() => useWizardNextGate('agents'))
    expect(result.current.disabled).toBe(false)
    expect(result.current.reason).toBeNull()
  })

  it('does not gate non-agent steps on personality', () => {
    useSetupWizardStore.setState({
      agents: [agent({ personality_preset: null })],
      stepsCompleted: completeStep('theme'),
    })
    const { result } = renderHook(() => useWizardNextGate('theme'))
    expect(result.current.disabled).toBe(false)
  })
})
