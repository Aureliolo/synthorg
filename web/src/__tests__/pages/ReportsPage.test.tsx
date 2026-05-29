import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect } from 'vitest'
import { apiError, apiPaginatedError } from '@/mocks/handlers'
import { paginatedEnvelopeFor } from '@/mocks/handlers/helpers'
import { server } from '@/test-setup'
import { useToastStore } from '@/stores/toast'
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
    server.use(
      http.get('/api/v1/reports/periods', () =>
        HttpResponse.json(paginatedEnvelopeFor<typeof listReportPeriods>([])),
      ),
    )
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

  it('generates a report and shows the result card when Generate is clicked', async () => {
    // Seed a single period so there is exactly one Generate button.
    server.use(
      http.get('/api/v1/reports/periods', () =>
        HttpResponse.json(paginatedEnvelopeFor<typeof listReportPeriods>(['daily'])),
      ),
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Generate' }))
    expect(await screen.findByText('Latest Daily report')).toBeInTheDocument()
  })

  it('surfaces an error toast when report generation fails', async () => {
    server.use(
      http.get('/api/v1/reports/periods', () =>
        HttpResponse.json(paginatedEnvelopeFor<typeof listReportPeriods>(['daily'])),
      ),
      http.post('/api/v1/reports/generate', () =>
        HttpResponse.json(apiError('generate boom'), { status: 500 }),
      ),
    )
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Generate' }))
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.title === 'Report generation failed'),
      ).toBe(true),
    )
    expect(screen.queryByText(/Latest .* report/)).not.toBeInTheDocument()
  })
})
