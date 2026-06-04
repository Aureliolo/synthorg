import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { apiError, buildConnection, successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { Connection } from '@/api/types/integrations'
import type { listWebhookActivity } from '@/api/endpoints/webhooks'
import { retryWebhookReceipt } from '@/api/endpoints/webhooks'

let connections: readonly Connection[]

vi.mock('@/hooks/useConnectionsData', () => ({
  useConnectionsData: () => ({ connections }),
}))

vi.mock('@/api/endpoints/webhooks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/endpoints/webhooks')>()),
  retryWebhookReceipt: vi.fn(() => Promise.resolve({
    status: 'accepted',
    event_type: 'workflow.executed',
    receipt_id: 'whr-000000000002',
  })),
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

  it('shows the no-deliveries empty state when activity is empty', async () => {
    connections = [makeConnection('slack-app')]
    server.use(
      http.get('/api/v1/webhooks/:connectionName/activity', () =>
        HttpResponse.json(successFor<typeof listWebhookActivity>([])),
      ),
    )
    renderPage()
    expect(await screen.findByText('No webhook deliveries yet')).toBeInTheDocument()
  })

  it('bulk-retries the selected failed receipt', async () => {
    connections = [makeConnection('slack-app')]
    renderPage()
    // The default activity handler returns one failed (retryable) receipt.
    const checkbox = await screen.findByLabelText('Select receipt whr-000000000002')
    fireEvent.click(checkbox)
    fireEvent.click(await screen.findByRole('button', { name: /Retry selected/ }))
    await waitFor(() =>
      expect(retryWebhookReceipt).toHaveBeenCalledWith('whr-000000000002'),
    )
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
