import { fireEvent, screen } from '@testing-library/react'
import { CompanyStep } from '@/pages/setup/CompanyStep'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { renderWithRouter } from '@/__tests__/test-utils'
import type { SetupCompanyResponse } from '@/api/types/setup'

function companyResponse(
  overrides: Partial<SetupCompanyResponse> = {},
): SetupCompanyResponse {
  return {
    company_name: 'Acme',
    description: null,
    template_applied: 'startup',
    department_count: 4,
    agent_count: 12,
    agents: [],
    ...overrides,
  }
}

describe('CompanyStep: applied state locks fields with a deliberate re-apply', () => {
  beforeEach(() => {
    useSetupWizardStore.getState().reset()
    useSetupWizardStore.setState({
      companyName: 'Acme',
      companyResponse: companyResponse(),
    })
  })

  it('disables the form and surfaces an Edit & re-apply toggle once applied', () => {
    renderWithRouter(<CompanyStep />, { initialEntries: ['/setup/company'] })

    expect(screen.getByLabelText(/company name/i)).toBeDisabled()
    expect(
      screen.getByRole('button', { name: /edit & re-apply/i }),
    ).toBeInTheDocument()
    // The plain "Apply Template" affordance is hidden in the locked state.
    expect(
      screen.queryByRole('button', { name: /^apply template$/i }),
    ).not.toBeInTheDocument()
  })

  it('re-enables the fields and shows Re-apply Template after Edit is clicked', () => {
    renderWithRouter(<CompanyStep />, { initialEntries: ['/setup/company'] })

    fireEvent.click(screen.getByRole('button', { name: /edit & re-apply/i }))

    expect(screen.getByLabelText(/company name/i)).not.toBeDisabled()
    expect(
      screen.getByRole('button', { name: /re-apply template/i }),
    ).toBeInTheDocument()
  })
})
