import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect } from 'vitest'
import { apiPaginatedError, emptyPage, paginatedFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import AdminAuditLogPage from '@/pages/AdminAuditLogPage'
import type { listAuditEntries } from '@/api/endpoints/audit'

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminAuditLogPage />
    </MemoryRouter>,
  )
}

describe('AdminAuditLogPage', () => {
  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByText('Security audit log')).toBeInTheDocument()
  })

  it('renders the evaluations table from the default handler', async () => {
    renderPage()
    expect(await screen.findByText('Recent evaluations')).toBeInTheDocument()
  })

  it('renders the empty state when no entries match', async () => {
    server.use(
      http.get('/api/v1/security/audit', () =>
        HttpResponse.json(paginatedFor<typeof listAuditEntries>(emptyPage())),
      ),
    )
    renderPage()
    expect(await screen.findByText('No audit entries match these filters')).toBeInTheDocument()
  })

  it('renders the error banner when the fetch fails', async () => {
    server.use(
      http.get('/api/v1/security/audit', () =>
        HttpResponse.json(apiPaginatedError('audit boom'), { status: 500 }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Could not load audit log')).toBeInTheDocument()
  })
})
