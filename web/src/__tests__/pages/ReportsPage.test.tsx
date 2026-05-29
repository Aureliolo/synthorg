import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, beforeEach } from 'vitest'
import { apiPaginatedError } from '@/mocks/handlers'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import { server } from '@/test-setup'
import ReportsPage from '@/pages/ReportsPage'
import type { listReportPeriods } from '@/api/endpoints/reports'

function renderPage() {
  return render(
    <MemoryRouter>
      <ReportsPage />
    </MemoryRouter>,
  )
}

describe('ReportsPage', () => {
  beforeEach(() => {
    // No default reports handler ships in the MSW set; give every test a
    // baseline empty-periods response so the mount fetch is satisfied.
    server.use(
      http.get('/api/v1/reports/periods', () =>
        HttpResponse.json(paginatedEnvelopeFor<typeof listReportPeriods>([])),
      ),
    )
  })

  it('renders the page heading', () => {
    renderPage()
    expect(screen.getByText('Reports')).toBeInTheDocument()
  })

  it('renders a card per reporting period', async () => {
    server.use(
      http.get('/api/v1/reports/periods', () =>
        HttpResponse.json(paginatedEnvelopeFor<typeof listReportPeriods>(['daily', 'monthly'])),
      ),
    )
    renderPage()
    expect(await screen.findByText('Daily')).toBeInTheDocument()
    expect(screen.getByText('Monthly')).toBeInTheDocument()
  })

  it('renders the empty state when no periods are available', async () => {
    renderPage()
    expect(await screen.findByText('No report periods available')).toBeInTheDocument()
  })

  it('renders the error banner when the periods fetch fails', async () => {
    server.use(
      http.get('/api/v1/reports/periods', () =>
        HttpResponse.json(apiPaginatedError('reports boom'), { status: 500 }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Could not load report periods')).toBeInTheDocument()
  })
})
