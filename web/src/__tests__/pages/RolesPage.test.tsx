import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { beforeEach, describe, expect, it } from 'vitest'
import RolesPage from '@/pages/RolesPage'
import { getCompanyConfig } from '@/api/endpoints/company'
import { successFor } from '@/mocks/handlers'
import { buildAgent } from '@/mocks/handlers/agents'
import { server } from '@/test-setup'
import { useCompanyStore } from '@/stores/company'
import type { CompanyConfig } from '@/api/types/org'

function configWithRoles(): CompanyConfig {
  return {
    company_name: 'Test Co',
    autonomy_level: 'supervised',
    budget_monthly: 0,
    communication_pattern: 'hub_and_spoke',
    departments: [],
    agents: [
      buildAgent({ name: 'a', role: 'Engineer' }),
      buildAgent({ name: 'b', role: 'Engineer' }),
      buildAgent({ name: 'c', role: 'Designer' }),
    ],
  }
}

describe('RolesPage', () => {
  beforeEach(() => {
    useCompanyStore.setState({ config: null, loading: false, error: null })
  })

  it('lists distinct roles and links each to its version history', async () => {
    server.use(
      http.get('/api/v1/company', () =>
        HttpResponse.json(successFor<typeof getCompanyConfig>(configWithRoles())),
      ),
    )
    render(
      <MemoryRouter>
        <RolesPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Designer')).toBeInTheDocument()
    expect(screen.getByText('Engineer')).toBeInTheDocument()

    const engineerLink = screen.getByRole('link', { name: /Engineer/i })
    expect(engineerLink).toHaveAttribute('href', '/roles/Engineer/versions')
  })

  it('shows an empty state when no agents have roles', async () => {
    server.use(
      http.get('/api/v1/company', () =>
        HttpResponse.json(
          successFor<typeof getCompanyConfig>(configWithRoles()),
        ),
      ),
    )
    useCompanyStore.setState({
      config: {
        company_name: 'Empty Co',
        autonomy_level: 'supervised',
        budget_monthly: 0,
        communication_pattern: 'hub_and_spoke',
        departments: [],
        agents: [],
      },
      loading: false,
      error: null,
    })

    render(
      <MemoryRouter>
        <RolesPage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('No roles defined')).toBeInTheDocument()
  })
})
