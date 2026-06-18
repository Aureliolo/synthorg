import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WizardNavigation } from '@/pages/setup/WizardNavigation'
import type { WizardStep } from '@/stores/setup-wizard'

const ORDER: readonly WizardStep[] = ['account', 'mode', 'template', 'complete']

describe('WizardNavigation', () => {
  it('hides the Next button when hideNext is set but keeps Back', () => {
    render(
      <WizardNavigation
        stepOrder={ORDER}
        currentStep="mode"
        onBack={vi.fn()}
        onNext={vi.fn()}
        hideNext
      />,
    )
    expect(screen.queryByRole('button', { name: /next/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back/i })).toBeInTheDocument()
  })

  it('enables Back on a non-first step', () => {
    render(
      <WizardNavigation
        stepOrder={ORDER}
        currentStep="mode"
        onBack={vi.fn()}
        onNext={vi.fn()}
        hideNext
      />,
    )
    expect(screen.getByRole('button', { name: /back/i })).not.toBeDisabled()
  })

  it('disables Back on the first step', () => {
    render(
      <WizardNavigation
        stepOrder={ORDER}
        currentStep="account"
        onBack={vi.fn()}
        onNext={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /back/i })).toBeDisabled()
  })

  it('renders Next on a normal step', () => {
    render(
      <WizardNavigation
        stepOrder={ORDER}
        currentStep="template"
        onBack={vi.fn()}
        onNext={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /next/i })).toBeInTheDocument()
  })
})
