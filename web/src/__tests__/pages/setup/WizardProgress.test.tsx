import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WizardProgress } from '@/pages/setup/WizardProgress'
import type { WizardStep } from '@/stores/setup-wizard'

const stepOrder: WizardStep[] = ['template', 'providers', 'company', 'agents', 'theme', 'complete']

const defaultStepsCompleted: Record<WizardStep, boolean> = {
  account: false,
  mode: false,
  template: false,
  company: false,
  providers: false,
  agents: false,
  theme: false,
  complete: false,
}

const defaultStepsNeedRevalidation: Record<WizardStep, boolean> = {
  account: false,
  mode: false,
  template: false,
  company: false,
  providers: false,
  agents: false,
  theme: false,
  complete: false,
}

describe('WizardProgress', () => {
  it('renders all step labels', () => {
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="template"
        stepsCompleted={defaultStepsCompleted}
        stepsNeedRevalidation={defaultStepsNeedRevalidation}
        canNavigateTo={() => false}
        onStepClick={() => {}}
      />,
    )
    expect(screen.getByText('Template')).toBeInTheDocument()
    expect(screen.getByText('Company')).toBeInTheDocument()
    expect(screen.getByText('Agents')).toBeInTheDocument()
    expect(screen.getByText('Providers')).toBeInTheDocument()
    expect(screen.getByText('Theme')).toBeInTheDocument()
    expect(screen.getByText('Done')).toBeInTheDocument()
  })

  it('marks active step with aria-current', () => {
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="company"
        stepsCompleted={defaultStepsCompleted}
        stepsNeedRevalidation={defaultStepsNeedRevalidation}
        canNavigateTo={() => true}
        onStepClick={() => {}}
      />,
    )
    const buttons = screen.getAllByRole('button')
    const companyButton = buttons.find((b) => b.textContent?.includes('Company'))
    expect(companyButton).toHaveAttribute('aria-current', 'step')
  })

  it('calls onStepClick when an accessible step is clicked', async () => {
    const handleClick = vi.fn()
    const user = userEvent.setup()
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="template"
        stepsCompleted={defaultStepsCompleted}
        stepsNeedRevalidation={defaultStepsNeedRevalidation}
        canNavigateTo={(step) => step === 'template'}
        onStepClick={handleClick}
      />,
    )
    const buttons = screen.getAllByRole('button')
    const templateButton = buttons.find((b) => b.textContent?.includes('Template'))!
    expect(templateButton).toBeInTheDocument()
    await user.click(templateButton)
    expect(handleClick).toHaveBeenCalledWith('template')
    expect(handleClick).toHaveBeenCalledTimes(1)
  })

  it('disables inaccessible steps', () => {
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="template"
        stepsCompleted={defaultStepsCompleted}
        stepsNeedRevalidation={defaultStepsNeedRevalidation}
        canNavigateTo={(step) => step === 'template'}
        onStepClick={() => {}}
      />,
    )
    const buttons = screen.getAllByRole('button')
    const companyButton = buttons.find((b) => b.textContent?.includes('Company'))
    expect(companyButton).toBeDisabled()
  })

  it('shows checkmark for completed steps', () => {
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="company"
        stepsCompleted={{ ...defaultStepsCompleted, template: true }}
        stepsNeedRevalidation={defaultStepsNeedRevalidation}
        canNavigateTo={() => true}
        onStepClick={() => {}}
      />,
    )
    // Template step should have a checkmark (Check icon renders as svg)
    const buttons = screen.getAllByRole('button')
    const templateButton = buttons.find((b) => b.textContent?.includes('Template'))!
    expect(templateButton).toBeInTheDocument()
    expect(templateButton.querySelector('svg')).toBeInTheDocument()
  })

  it('renders a warning indicator on a complete step that needs revalidation', () => {
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="theme"
        stepsCompleted={{ ...defaultStepsCompleted, agents: true }}
        stepsNeedRevalidation={{ ...defaultStepsNeedRevalidation, agents: true }}
        canNavigateTo={() => true}
        onStepClick={() => {}}
      />,
    )
    const buttons = screen.getAllByRole('button')
    const agentsButton = buttons.find((b) => b.textContent?.includes('Agents'))!
    // The sr-only revalidation hint is referenced via aria-describedby.
    expect(agentsButton).toHaveAttribute('aria-describedby', 'agents-needs-revalidation')
    expect(screen.getByText(/Needs review/i)).toBeInTheDocument()
  })

  it('does NOT render the warning indicator on the active step (the user is fixing it)', () => {
    render(
      <WizardProgress
        stepOrder={stepOrder}
        currentStep="agents"
        stepsCompleted={{ ...defaultStepsCompleted, agents: true }}
        stepsNeedRevalidation={{ ...defaultStepsNeedRevalidation, agents: true }}
        canNavigateTo={() => true}
        onStepClick={() => {}}
      />,
    )
    const buttons = screen.getAllByRole('button')
    const agentsButton = buttons.find((b) => b.textContent?.includes('Agents'))!
    expect(agentsButton).not.toHaveAttribute('aria-describedby')
  })

  it('renders with account step when included in stepOrder', () => {
    const withAccount: WizardStep[] = ['account', ...stepOrder]
    render(
      <WizardProgress
        stepOrder={withAccount}
        currentStep="account"
        stepsCompleted={defaultStepsCompleted}
        stepsNeedRevalidation={defaultStepsNeedRevalidation}
        canNavigateTo={() => false}
        onStepClick={() => {}}
      />,
    )
    expect(screen.getByText('Account')).toBeInTheDocument()
  })
})
