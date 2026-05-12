import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WizardModeStep } from '@/pages/setup/WizardModeStep'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { renderWithRouter } from '@/__tests__/test-utils'

describe('WizardModeStep', () => {
  beforeEach(() => {
    useSetupWizardStore.getState().reset()
  })

  it('does not auto-mark the mode step complete on mount', () => {
    renderWithRouter(<WizardModeStep />, { initialEntries: ['/setup/mode'] })
    // An auto-mark on mount would let the user skip the step without
    // making a choice; require an explicit click before Continue lights up.
    expect(useSetupWizardStore.getState().stepsCompleted.mode).toBe(false)
  })

  it('marks the mode step complete and navigates when the user clicks an option', () => {
    const { router } = renderWithRouter(<WizardModeStep />, {
      initialEntries: ['/setup/mode'],
    })

    const guidedOption = screen.getByRole('button', { name: /Guided Setup/i })
    fireEvent.click(guidedOption)

    expect(useSetupWizardStore.getState().stepsCompleted.mode).toBe(true)
    expect(useSetupWizardStore.getState().wizardMode).toBe('guided')
    // Auto-advances to the next step after mode selection.
    expect(router.state.location.pathname).toBe('/setup/template')
  })

  it('switches to quick mode and lands on the first quick-mode step', () => {
    const { router } = renderWithRouter(<WizardModeStep />, {
      initialEntries: ['/setup/mode'],
    })

    const quickOption = screen.getByRole('button', { name: /Quick Setup/i })
    fireEvent.click(quickOption)

    expect(useSetupWizardStore.getState().wizardMode).toBe('quick')
    expect(router.state.location.pathname).toBe('/setup/providers')
  })

  it('selects mode via keyboard (Enter) so the click-required contract is keyboard-equivalent', async () => {
    const user = userEvent.setup()
    const { router } = renderWithRouter(<WizardModeStep />, {
      initialEntries: ['/setup/mode'],
    })

    const guidedOption = screen.getByRole('button', { name: /Guided Setup/i })
    guidedOption.focus()
    await user.keyboard('{Enter}')

    expect(useSetupWizardStore.getState().stepsCompleted.mode).toBe(true)
    expect(useSetupWizardStore.getState().wizardMode).toBe('guided')
    expect(router.state.location.pathname).toBe('/setup/template')
  })

  it('selects mode via keyboard (Space) so the click-required contract is keyboard-equivalent', async () => {
    const user = userEvent.setup()
    const { router } = renderWithRouter(<WizardModeStep />, {
      initialEntries: ['/setup/mode'],
    })

    const quickOption = screen.getByRole('button', { name: /Quick Setup/i })
    quickOption.focus()
    await user.keyboard(' ')

    expect(useSetupWizardStore.getState().wizardMode).toBe('quick')
    expect(router.state.location.pathname).toBe('/setup/providers')
  })
})
