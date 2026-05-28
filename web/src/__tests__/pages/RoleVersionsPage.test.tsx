import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import RoleVersionsPage from '@/pages/RoleVersionsPage'

describe('RoleVersionsPage', () => {
  it('renders the role version timeline from the URL param', async () => {
    render(
      <MemoryRouter initialEntries={['/roles/Lead%20Developer/versions']}>
        <Routes>
          <Route path="/roles/:roleName/versions" element={<RoleVersionsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('Versions for Lead Developer')).toBeInTheDocument()
    // Default MSW returns an empty page -> empty-state copy.
    expect(await screen.findByText('No version history yet')).toBeInTheDocument()
  })

  it('shows an error when the role name is missing from the URL', () => {
    render(
      <MemoryRouter>
        <RoleVersionsPage />
      </MemoryRouter>,
    )

    expect(screen.getByText('Missing role in URL')).toBeInTheDocument()
  })
})
