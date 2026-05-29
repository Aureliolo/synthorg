import { fireEvent, render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect } from 'vitest'
import { apiPaginatedError, buildAuditEntry, emptyPage, paginatedFor } from '@/mocks/handlers'
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

  it('re-fetches with the tool filter and shows the filtered-empty state', async () => {
    renderPage()
    await screen.findByText('Recent evaluations')
    // The filtered query returns nothing.
    server.use(
      http.get('/api/v1/security/audit', () =>
        HttpResponse.json(paginatedFor<typeof listAuditEntries>(emptyPage())),
      ),
    )
    fireEvent.change(screen.getByLabelText('Tool name'), {
      target: { value: 'nonexistent.tool' },
    })
    expect(
      await screen.findByText('No audit entries match these filters'),
    ).toBeInTheDocument()
  })

  it('renders the Load more button when the page reports more entries', async () => {
    server.use(
      http.get('/api/v1/security/audit', () =>
        HttpResponse.json(
          paginatedFor<typeof listAuditEntries>({
            data: [buildAuditEntry()],
            limit: 50,
            nextCursor: 'cursor-2',
            hasMore: true,
            pagination: { limit: 50, next_cursor: 'cursor-2', has_more: true },
          }),
        ),
      ),
    )
    renderPage()
    expect(await screen.findByRole('button', { name: 'Load more' })).toBeInTheDocument()
  })
})
