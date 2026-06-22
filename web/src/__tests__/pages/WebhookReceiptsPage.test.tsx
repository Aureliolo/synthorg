import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { apiError, buildConnection, emptyPage, paginatedFor, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { Connection } from '@/api/types/integrations'
import type { listWebhookActivity, WebhookReceipt } from '@/api/endpoints/webhooks'
import { retryWebhookReceipt } from '@/api/endpoints/webhooks'

let connections: readonly Connection[]

vi.mock('@/hooks/useConnectionsData', () => ({
  useConnectionsData: () => ({ connections }),
}))

const { default: WebhookReceiptsPage } = await import('@/pages/WebhookReceiptsPage')

function makeConnection(name: string): Connection {
  return buildConnection({ name })
}

function renderPage() {
  return render(
    <MemoryRouter>
      <WebhookReceiptsPage />
    </MemoryRouter>,
  )
}

describe('WebhookReceiptsPage', () => {
  beforeEach(() => {
    connections = []
    vi.clearAllMocks()
  })

  it('renders the page heading', () => {
    renderPage()
    expect(
      screen.getByRole('heading', { name: /webhook receipts/i }),
    ).toBeInTheDocument()
  })

  it('shows the no-connections empty state when there are no connections', () => {
    connections = []
    renderPage()
    expect(screen.getByText('No connections configured')).toBeInTheDocument()
  })

  it('renders the receipts table once a connection is selected and activity loads', async () => {
    connections = [makeConnection('slack-app')]
    renderPage()
    // The default activity handler returns rows, so the table renders.
    expect(await screen.findByText('Recent receipts')).toBeInTheDocument()
  })

  it('appends the next page when Load more is clicked', async () => {
    connections = [makeConnection('slack-app')]
    const pageOne: WebhookReceipt = {
      id: '00000000-0000-0000-0000-0000000000a1',
      connection_name: 'slack-app',
      event_type: 'page-one.event',
      status: 'completed',
      received_at: '2026-04-30T10:00:00Z',
      processed_at: '2026-04-30T10:00:01Z',
      payload_json: '{}',
      error: null,
    }
    const pageTwo: WebhookReceipt = {
      ...pageOne,
      id: '00000000-0000-0000-0000-0000000000a2',
      event_type: 'page-two.event',
    }
    server.use(
      http.get('/api/v1/webhooks/:connectionName/activity', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        if (cursor === 'cursor-2') {
          return HttpResponse.json(
            paginatedFor<typeof listWebhookActivity>({
              ...emptyPage<WebhookReceipt>(),
              data: [pageTwo],
            }),
          )
        }
        return HttpResponse.json(
          paginatedFor<typeof listWebhookActivity>({
            ...emptyPage<WebhookReceipt>(),
            data: [pageOne],
            nextCursor: 'cursor-2',
            hasMore: true,
          }),
        )
      }),
    )
    renderPage()
    expect(await screen.findByText('page-one.event')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: /Load more/ }))
    // The second page appends to (does not replace) the first.
    expect(await screen.findByText('page-two.event')).toBeInTheDocument()
    expect(screen.getByText('page-one.event')).toBeInTheDocument()
  })

  it('shows the no-deliveries empty state when activity is empty', async () => {
    connections = [makeConnection('slack-app')]
    server.use(
      http.get('/api/v1/webhooks/:connectionName/activity', () =>
        HttpResponse.json(
          paginatedFor<typeof listWebhookActivity>(emptyPage<WebhookReceipt>()),
        ),
      ),
    )
    renderPage()
    expect(await screen.findByText('No webhook deliveries yet')).toBeInTheDocument()
  })

  it('bulk-retries the selected failed receipt', async () => {
    let retriedId: string | undefined
    server.use(
      http.post('/api/v1/webhooks/receipts/:id/retry', ({ params }) => {
        retriedId = String(params['id'])
        return HttpResponse.json(
          successFor<typeof retryWebhookReceipt>({
            status: 'accepted',
            event_type: 'workflow.executed',
            receipt_id: '00000000-0000-0000-0000-000000000002',
          }),
        )
      }),
    )
    connections = [makeConnection('slack-app')]
    renderPage()
    // The default activity handler returns one failed (retryable) receipt.
    const checkbox = await screen.findByLabelText('Select receipt 00000000-0000-0000-0000-000000000002')
    fireEvent.click(checkbox)
    fireEvent.click(await screen.findByRole('button', { name: /Retry selected/ }))
    await waitFor(() => {
      expect(retriedId).toBe('00000000-0000-0000-0000-000000000002')
    })
  })

  it('shows the error banner when the activity fetch fails', async () => {
    connections = [makeConnection('slack-app')]
    server.use(
      http.get('/api/v1/webhooks/:connectionName/activity', () =>
        HttpResponse.json(apiError('activity boom'), { status: 500 }),
      ),
    )
    renderPage()
    expect(
      await screen.findByText('Could not load webhook activity'),
    ).toBeInTheDocument()
  })
})
